import { useEffect, useRef, useState } from 'react'
import ProductCard from './ProductCard.jsx'

const SUGGESTIONS = [
  'Lightweight laptop for code, under ₹90k',
  'Noise-cancelling headphones for flights',
  'A camera I can travel light with',
  'Mechanical keyboard, quiet switches',
]

export default function Concierge({ onLoadingChange, onTalk }) {
  const [text, setText] = useState('')
  const [hint, setHint] = useState('')
  const [hintErr, setHintErr] = useState(false)
  const [listening, setListening] = useState(false)
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState(null)
  const [resultQuery, setResultQuery] = useState('')

  const recognitionRef = useRef(null)
  const baseTextRef = useRef('')
  const watchdogRef = useRef(null)
  const [voiceSupported, setVoiceSupported] = useState(false)

  // --- Browser-native voice input (Web Speech API), ported from the original app ---
  useEffect(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SR) return
    setVoiceSupported(true)

    const recognition = new SR()
    recognition.lang = 'en-IN'
    recognition.interimResults = true
    recognition.continuous = false

    recognition.addEventListener('start', () => {
      clearTimeout(watchdogRef.current)  // real engine responded — cancel the unsupported-browser warning
      setListening(true)
      setHintErr(false)
      setHint('Listening… tap the mic again to stop.')
    })
    recognition.addEventListener('result', (event) => {
      let transcript = ''
      for (let i = 0; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript
      }
      setText(baseTextRef.current + transcript)
    })
    recognition.addEventListener('error', (event) => {
      clearTimeout(watchdogRef.current)  // engine responded (with an error) — it exists, so cancel the warning
      setHintErr(true)
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        setHint('Microphone access was blocked. Allow it in your browser to use voice input.')
      } else if (event.error === 'no-speech') {
        setHint('We didn’t catch that. Tap the mic and try again.')
      } else {
        setHint('Voice input error: ' + event.error)
      }
    })
    recognition.addEventListener('end', () => {
      setListening(false)
      setHint((h) => (h === 'Listening… tap the mic again to stop.' ? '' : h))
    })

    recognitionRef.current = recognition
    return () => {
      clearTimeout(watchdogRef.current)
      recognition.abort()
    }
  }, [])

  const toggleMic = () => {
    const recognition = recognitionRef.current
    if (!recognition) return
    if (listening) {
      recognition.stop()
    } else {
      baseTextRef.current = text ? text.trim() + ' ' : ''
      const unsupported = () => {
        setHintErr(true)
        setHint('Voice input isn’t available in this browser. Try Chrome or Edge.')
        setListening(false)
      }
      try {
        recognition.start()
        // Chromium forks like Opera/Brave expose webkitSpeechRecognition but never
        // actually run it — start() no-ops and no event ever fires. If neither 'start'
        // nor 'error' arrives shortly, treat the browser as unsupported and say so
        // instead of silently doing nothing. (Self-corrects if 'start' arrives late.)
        clearTimeout(watchdogRef.current)
        watchdogRef.current = setTimeout(unsupported, 1500)
      } catch (_) {
        // start() throws if already started — but if we're not listening, the engine
        // isn't really there, so surface the same guidance.
        unsupported()
      }
    }
  }

  const setLoad = (v) => {
    setLoading(v)
    onLoadingChange?.(v)
  }

  const submit = async (e) => {
    e?.preventDefault()
    const query = text.trim()
    if (!query) {
      setHintErr(true)
      setHint('Tell the concierge what you’re looking for first.')
      return
    }
    setLoad(true)
    setHintErr(false)
    setHint('Curating your edit…')

    try {
      const res = await fetch('/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: query }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'Request failed')

      const list = data.results || []
      setResults(list)
      setResultQuery(query)
      setHint(`Curated ${list.length} picks for “${query}”.`)
    } catch (err) {
      setHintErr(true)
      // Friendly note: the standalone UI runs without the Flask backend.
      setHint(
        err.message?.includes('Failed to fetch') || err instanceof TypeError
          ? 'The concierge is offline right now. Please try again in a moment.'
          : err.message,
      )
    } finally {
      setLoad(false)
    }
  }

  return (
    <section className="section concierge" id="concierge">
      <div className="wrap">
        <div className="concierge-head">
          <span className="eyebrow">The concierge</span>
          <h2>Describe it. We’ll find the one.</h2>
          <p>
            Speak or type what you’re after. Budget, use, the feel of it. Our
            concierge reads intent, not keywords, and returns a short, considered edit.
          </p>
        </div>

        <form className="concierge-box" onSubmit={submit}>
          <div className="concierge-field">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="I want a lightweight laptop for programming, under ₹90,000, with great battery and a bright screen…"
            />
            {voiceSupported && (
              <button
                type="button"
                className={`mic-btn ${listening ? 'listening' : ''}`}
                onClick={toggleMic}
                title="Speak your description"
                aria-label="Speak your description"
              >
                ▮▮
              </button>
            )}
          </div>

          <div className="concierge-bar">
            <div className="chips">
              {SUGGESTIONS.map((s) => (
                <button
                  type="button"
                  key={s}
                  className="chip"
                  onClick={() => setText(s)}
                >
                  {s}
                </button>
              ))}
            </div>
            <button type="submit" className="btn btn-solid" disabled={loading}>
              {loading ? 'Curating…' : 'Curate'} <span className="arrow">→</span>
            </button>
          </div>

          <div className={`concierge-hint ${hintErr ? 'err' : ''}`}>{hint}</div>
        </form>

        {results && results.length > 0 && (
          <div className="concierge-results">
            <div className="sec-head">
              <div>
                <span className="eyebrow">Curated for “{resultQuery}”</span>
                <h2>Your edit</h2>
              </div>
              <p>Hand-ranked by the concierge from your description.</p>
            </div>

            <div className="product-grid">
              {results.map((p, i) => (
                <ProductCard key={p.id || p.name || i} product={p} index={i} onTalk={onTalk} />
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
