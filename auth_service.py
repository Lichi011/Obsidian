"""Google OAuth login + stateless JWT sessions.

Flow: /auth/login redirects to Google; Google redirects back to /auth/callback with an
authorization code; we exchange it, read the *verified* email/sub from Google's ID token,
mint our OWN short-lived HS256 JWT, and set it as an httpOnly cookie. Every protected
request verifies that cookie's JWT — there is no server-side session store.

The browser never holds the signing secret; it just carries the token and re-sends it.
The server signs once (at login) and re-verifies the signature on each request.

Requires GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET (from a Google Cloud OAuth client) and a
JWT_SECRET in .env. If the Google creds are missing, login endpoints return 503 and the
rest of the app keeps working.
"""

import os
import time
from functools import wraps

import jwt
from authlib.integrations.flask_client import OAuth
from flask import jsonify, request

# --- config -------------------------------------------------------------------
JWT_SECRET = os.environ.get('JWT_SECRET', 'dev-insecure-change-me')
JWT_ALG = 'HS256'
JWT_TTL_SECONDS = int(os.environ.get('JWT_TTL_HOURS', '24')) * 3600
COOKIE_NAME = 'obsidian_session'

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')

_oauth = OAuth()


def google_configured() -> bool:
    """True only if the Google OAuth client is set up, so callers can 503 gracefully."""
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def init_auth(app):
    """Register the Google OAuth client on the Flask app. Call once at startup."""
    _oauth.init_app(app)
    if google_configured():
        _oauth.register(
            name='google',
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            client_kwargs={'scope': 'openid email profile'},
        )
    return _oauth


def google_client():
    return _oauth.google


# --- JWT ----------------------------------------------------------------------
def mint_jwt(sub: str, email: str, name: str = '') -> str:
    """Sign a short-lived session token for a logged-in user (server-side, at login)."""
    now = int(time.time())
    return jwt.encode(
        {'sub': sub, 'email': email, 'name': name, 'iat': now, 'exp': now + JWT_TTL_SECONDS},
        JWT_SECRET,
        algorithm=JWT_ALG,
    )


def verify_jwt(token: str):
    """Return the claims if the signature is valid and not expired, else None."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except Exception:
        return None


def current_user():
    """Claims dict from the session cookie's JWT, or None if missing/invalid/expired."""
    token = request.cookies.get(COOKIE_NAME)
    return verify_jwt(token) if token else None


def require_auth(fn):
    """Decorator: reject the request with 401 unless a valid session JWT is present."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return jsonify({'error': 'Please sign in.'}), 401
        return fn(*args, **kwargs)
    return wrapper


def set_session_cookie(resp, token: str, secure: bool):
    """Attach the session JWT as an httpOnly cookie (not localStorage — avoids XSS theft)."""
    resp.set_cookie(
        COOKIE_NAME, token,
        httponly=True, samesite='Lax', secure=secure, max_age=JWT_TTL_SECONDS, path='/',
    )
    return resp


def clear_session_cookie(resp):
    resp.delete_cookie(COOKIE_NAME, path='/')
    return resp
