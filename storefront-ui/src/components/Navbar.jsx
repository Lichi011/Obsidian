import { useEffect, useState } from 'react'

export default function Navbar({ quota }) {
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  const hasQuota = quota && quota.remaining != null
  const low = hasQuota && quota.remaining <= 10

  return (
    <nav className={`nav ${scrolled ? 'scrolled' : ''}`}>
      <div className="wrap nav-inner">
        <a href="#top" className="brand">
          <span className="brand-dot" />
          OBSIDIAN
        </a>

        {hasQuota && (
          <div
            className={`nav-quota ${low ? 'low' : ''}`}
            title="Reddit API requests remaining this month (free tier)"
          >
            <span className="nav-quota-dot" />
            <span className="nav-quota-label">Reddit API</span>
            <span className="nav-quota-count mono">{quota.remaining}/{quota.limit}</span>
          </div>
        )}
      </div>
    </nav>
  )
}
