import { useState } from 'react'

// Deterministic glow fallback for products coming from the backend (which carry no
// `glow`), so live recommendations still render premium visuals.
const FALLBACK_GLOWS = [
  'radial-gradient(120% 120% at 30% 20%, #ff2bd6 0%, #7a0b6b 35%, #120012 70%)',
  'radial-gradient(120% 120% at 70% 20%, #2bf6ff 0%, #0b6a7a 38%, #001214 70%)',
  'radial-gradient(120% 120% at 50% 15%, #c6ff3d 0%, #5a7a0b 38%, #0a1200 70%)',
  'radial-gradient(120% 120% at 60% 20%, #2b7bff 0%, #0b317a 38%, #000812 70%)',
  'radial-gradient(120% 120% at 40% 20%, #b02bff 0%, #420b7a 38%, #0a0012 70%)',
  'radial-gradient(120% 120% at 35% 25%, #ffae2b 0%, #7a4a0b 38%, #120c00 70%)',
]

export default function ProductCard({ product, index, onTalk, user, onSignIn }) {
  const glow = product.glow || FALLBACK_GLOWS[index % FALLBACK_GLOWS.length]
  const initial = (product.name || '?').trim().charAt(0).toUpperCase()

  // Price-watch toggle: closed -> form -> watching. The email now comes from the
  // logged-in session (server-side), so the form only needs an optional target price.
  const [watchOpen, setWatchOpen] = useState(false)
  const [watching, setWatching] = useState(false)
  const [target, setTarget] = useState('')
  const [watchMsg, setWatchMsg] = useState('')
  const [watchErr, setWatchErr] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const enableWatch = async (e) => {
    e?.preventDefault()
    setSubmitting(true)
    setWatchErr(false)
    try {
      const res = await fetch('/watch/enable', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          product: product.name,
          url: product.purchase_link || '',
          baseline_price: product.price || '',
          target_price: target.trim() || null,
        }),
      })
      const data = await res.json()
      if (res.status === 401) throw new Error('Please sign in to watch prices.')
      if (!res.ok) throw new Error(data.error || 'Could not start the watch.')
      setWatching(true)
      setWatchOpen(false)
      setWatchErr(false)
      const who = user?.email || 'you'
      setWatchMsg(
        target.trim()
          ? `Watching — we’ll email ${who} when it hits ₹${target.trim()}.`
          : `Watching — we’ll email ${who} if the price drops.`,
      )
    } catch (err) {
      setWatchErr(true)
      setWatchMsg(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const disableWatch = async () => {
    try {
      await fetch('/watch/disable', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product: product.name }),   // scoped to session on the server
      })
    } catch (_) { /* best-effort */ }
    setWatching(false)
    setWatchMsg('Price watch turned off.')
    setWatchErr(false)
  }

  return (
    <article className="card">
      <div className="card-visual">
        {product.badge && <span className="card-badge">{product.badge}</span>}
        <div className="card-glow" style={{ background: glow }} />
        <span className="ghost display">{initial}</span>
      </div>

      <div className="card-cat">{product.category || product.source || 'Curated'}</div>
      <h3 className="card-name">{product.name}</h3>
      <p className="card-desc">{product.description}</p>

      <div className="card-foot">
        <span className="card-price">{product.price || '—'}</span>
        <span className="card-source">{product.source}</span>
      </div>

      {product.purchase_link && (
        <a
          className="btn btn-solid card-buy"
          href={product.purchase_link}
          target="_blank"
          rel="noreferrer"
        >
          View on {product.source || 'store'} <span className="arrow">↗</span>
        </a>
      )}

      {onTalk && (
        <button className="btn card-talk" onClick={() => onTalk(product)}>
          Talk to this product <span className="arrow">→</span>
        </button>
      )}

      {/* Price-watch control — requires a logged-in user */}
      {!user ? (
        <button className="btn card-watch" onClick={onSignIn}>
          Sign in to watch price
        </button>
      ) : watching ? (
        <div className="watch-active">
          <span className="watch-line">{watchMsg}</span>
          <button className="watch-off" onClick={disableWatch}>Stop watching</button>
        </div>
      ) : watchOpen ? (
        <form className="watch-form" onSubmit={enableWatch}>
          <input
            type="text"
            inputMode="numeric"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="Target price (optional)"
            autoFocus
          />
          <div className="watch-form-row">
            <button type="submit" className="btn btn-solid" disabled={submitting}>
              {submitting ? 'Setting…' : 'Notify me'}
            </button>
            <button type="button" className="watch-off" onClick={() => setWatchOpen(false)}>
              Cancel
            </button>
          </div>
          {watchMsg && <span className={`watch-line ${watchErr ? 'err' : ''}`}>{watchMsg}</span>}
        </form>
      ) : (
        <button className="btn card-watch" onClick={() => setWatchOpen(true)}>
          Watch price
        </button>
      )}
      {user && !watchOpen && !watching && watchMsg && (
        <span className={`watch-line ${watchErr ? 'err' : ''}`}>{watchMsg}</span>
      )}
    </article>
  )
}
