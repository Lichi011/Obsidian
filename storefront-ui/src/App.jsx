import { useEffect, useState } from 'react'
import Navbar from './components/Navbar.jsx'
import Hero from './components/Hero.jsx'
import Marquee from './components/Marquee.jsx'
import Concierge from './components/Concierge.jsx'
import Footer from './components/Footer.jsx'
import TalkDrawer from './components/TalkDrawer.jsx'
import WatchPanel from './components/WatchPanel.jsx'
import LoginGate from './components/LoginGate.jsx'
import './styles/components.css'

export default function App() {
  // The product the user is currently "talking to" (null = drawer closed).
  const [talkProduct, setTalkProduct] = useState(null)
  // Whether the "Watching" panel (list of the visitor's price watches) is open.
  const [watchesOpen, setWatchesOpen] = useState(false)
  // RapidAPI request quota, shown globally so the user always knows what's left.
  const [quota, setQuota] = useState(null)
  // Logged-in user ({email, name}) or null. Comes from the JWT session cookie.
  const [user, setUser] = useState(null)
  // Whether the initial /auth/me check has resolved (avoids flashing the login gate).
  const [authChecked, setAuthChecked] = useState(false)

  // Seed the quota meter once signed in (known after any Reddit call).
  useEffect(() => {
    if (!user) return
    fetch('/talk/quota')
      .then((r) => r.json())
      .then((q) => { if (q && q.remaining != null) setQuota(q) })
      .catch(() => {})
  }, [user])

  // Restore the session on load: ask the server who (if anyone) is logged in.
  useEffect(() => {
    fetch('/auth/me')
      .then((r) => r.json())
      .then((d) => { if (d && d.authenticated) setUser({ email: d.email, name: d.name }) })
      .catch(() => {})
      .finally(() => setAuthChecked(true))
  }, [])

  // Google login is a full-page redirect (OAuth needs top-level navigation).
  const signIn = () => { window.location.href = '/auth/login' }
  const signOut = async () => {
    try { await fetch('/auth/logout', { method: 'POST' }) } catch (_) { /* ignore */ }
    setUser(null)
    setWatchesOpen(false)
  }

  // Hold the UI until we know the auth state, then gate everything behind sign-in.
  if (!authChecked) {
    return <div className="auth-splash"><div className="spinner" /></div>
  }
  if (!user) {
    return <LoginGate onSignIn={signIn} />
  }

  return (
    <>
      <Navbar
        quota={quota}
        user={user}
        onSignIn={signIn}
        onSignOut={signOut}
        onOpenWatches={() => setWatchesOpen(true)}
      />

      <main>
        <Hero />
        <Marquee />
        <Concierge onTalk={setTalkProduct} user={user} onSignIn={signIn} />
      </main>

      <Footer />

      <TalkDrawer
        product={talkProduct}
        onClose={() => setTalkProduct(null)}
        onQuota={setQuota}
      />

      <WatchPanel
        open={watchesOpen}
        onClose={() => setWatchesOpen(false)}
        user={user}
        onSignIn={signIn}
      />
    </>
  )
}
