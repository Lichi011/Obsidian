import { useEffect, useState } from 'react'

// "Watching" panel: lists the logged-in user's price watches and lets them stop any.
// The user is identified by the JWT session cookie, so /watch/list needs no email param —
// the server scopes it to the session. Removes via /watch/disable.
export default function WatchPanel({ open, onClose, user, onSignIn }) {
  const [watches, setWatches] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // Load the user's active watches each time the panel opens.
  useEffect(() => {
    if (!open || !user) {
      setWatches([])
      return
    }
    let cancelled = false
    setLoading(true)
    setError('')
    fetch('/watch/list')
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return
        setWatches((data.watches || []).filter((w) => w.enabled))
      })
      .catch(() => { if (!cancelled) setError('Could not load your watches.') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [open, user])

  // Optimistically drop it from the list, then tell the backend.
  const stopWatching = async (w) => {
    setWatches((prev) => prev.filter((x) => x.id !== w.id))
    try {
      await fetch('/watch/disable', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: w.id }),
      })
    } catch (_) { /* best-effort — already removed from view */ }
  }

  return (
    <>
      <div className={`drawer-overlay ${open ? 'open' : ''}`} onClick={onClose} />
      <aside className={`talk-drawer ${open ? 'open' : ''}`} aria-hidden={!open}>
        <div className="talk-head">
          <div>
            <span className="eyebrow">Your price watches</span>
            <h3>Watching{watches.length > 0 ? ` (${watches.length})` : ''}</h3>
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        <div className="talk-body">
          {!user && (
            <div className="talk-status">
              <p>Sign in to see your watches.</p>
              <span className="talk-status-sub">
                Your price watches are tied to your Google account.
              </span>
              <button className="btn btn-solid" style={{ marginTop: '1rem' }} onClick={onSignIn}>
                Sign in with Google
              </button>
            </div>
          )}

          {user && loading && (
            <div className="talk-status">
              <div className="spinner" />
              <p>Loading your watches…</p>
            </div>
          )}

          {user && !loading && error && (
            <div className="talk-status err"><p>{error}</p></div>
          )}

          {user && !loading && !error && watches.length === 0 && (
            <div className="talk-status">
              <p>No active watches.</p>
              <span className="talk-status-sub">
                Set a price watch on any product and it’ll show up here.
              </span>
            </div>
          )}

          {user && !loading && watches.length > 0 && (
            <ul className="watch-list">
              {watches.map((w) => (
                <li key={w.id} className="watch-item">
                  <div className="watch-item-info">
                    <span className="watch-item-name">{w.product}</span>
                    <span className="watch-item-meta">
                      {w.target_price != null
                        ? `Notify at ₹${w.target_price}`
                        : 'Notify on any drop'}
                      {w.last_price != null ? ` · last ₹${w.last_price}` : ''}
                    </span>
                  </div>
                  <button
                    className="watch-item-remove"
                    onClick={() => stopWatching(w)}
                    aria-label={`Stop watching ${w.product}`}
                  >
                    Stop
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
    </>
  )
}
