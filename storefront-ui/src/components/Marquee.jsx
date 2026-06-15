const phrases = [
  'Crafted for the few',
  'No algorithms, only taste',
  'Buy once, buy right',
  'Curated, never cluttered',
]

export default function Marquee() {
  // Duplicate the list so the -50% translate loops seamlessly.
  const items = [...phrases, ...phrases]
  return (
    <div className="marquee" aria-hidden="true">
      <div className="marquee-track">
        {items.map((p, i) => (
          <span className="marquee-item" key={i}>
            {p}
            <span className="sep">✳</span>
          </span>
        ))}
      </div>
    </div>
  )
}
