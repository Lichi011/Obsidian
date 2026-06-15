const stats = [
  { num: '600+', lbl: 'Curated products' },
  { num: '24/7', lbl: 'AI concierge' },
  { num: '48h', lbl: 'Member delivery' },
]

export default function Hero() {
  return (
    <header className="hero" id="top">
      <div className="hero-bg" />
      <div className="hero-grid" />
      <div className="wrap hero-inner rise">
        <h1 className="display">
          Own less. <em>Own better.</em>
        </h1>

        <p className="hero-sub">
          A curated tech marketplace where an AI concierge reads what you actually
          need and surfaces only the products worth owning. No noise. No clutter.
          Just the edit.
        </p>

        <div className="hero-cta">
          <a href="#concierge" className="btn">
            Ask the concierge <span className="arrow">→</span>
          </a>
        </div>

        <div className="hero-stats">
          {stats.map((s) => (
            <div className="stat" key={s.lbl}>
              <div className="num display">{s.num}</div>
              <div className="lbl">{s.lbl}</div>
            </div>
          ))}
        </div>
      </div>
    </header>
  )
}
