import { useEffect, useState } from 'react'
import Navbar from './components/Navbar.jsx'
import Hero from './components/Hero.jsx'
import Marquee from './components/Marquee.jsx'
import Concierge from './components/Concierge.jsx'
import Footer from './components/Footer.jsx'
import TalkDrawer from './components/TalkDrawer.jsx'
import './styles/components.css'

export default function App() {
  // The product the user is currently "talking to" (null = drawer closed).
  const [talkProduct, setTalkProduct] = useState(null)
  // RapidAPI request quota, shown globally so the user always knows what's left.
  const [quota, setQuota] = useState(null)

  // Seed the quota meter on load (known once any Reddit call has happened).
  useEffect(() => {
    fetch('/talk/quota')
      .then((r) => r.json())
      .then((q) => { if (q && q.remaining != null) setQuota(q) })
      .catch(() => {})
  }, [])

  return (
    <>
      <Navbar quota={quota} />

      <main>
        <Hero />
        <Marquee />
        <Concierge onTalk={setTalkProduct} />
      </main>

      <Footer />

      <TalkDrawer
        product={talkProduct}
        onClose={() => setTalkProduct(null)}
        onQuota={setQuota}
      />
    </>
  )
}
