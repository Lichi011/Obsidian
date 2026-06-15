// Curated demo catalog. Each product carries a `glow` gradient used to render its
// hero visual — accent colors live only in imagery, never in UI chrome (CRED rule).
// Shape mirrors the Flask /search response (name, price, source, description) so the
// same cards render real recommendations once wired to the backend.

export const products = [
  {
    id: 'p1',
    name: 'Sony WH-1000XM5',
    category: 'Audio',
    price: '₹26,990',
    source: 'Amazon',
    description: 'Industry-leading noise cancellation with 30-hour battery and adaptive sound.',
    badge: 'Editor’s pick',
    glow: 'radial-gradient(120% 120% at 30% 20%, #ff2bd6 0%, #7a0b6b 35%, #120012 70%)',
  },
  {
    id: 'p2',
    name: 'Apple MacBook Air M3',
    category: 'Compute',
    price: '₹1,14,900',
    source: 'Flipkart',
    description: 'Fanless 13-inch ultrabook, 18-hour battery, built for all-day programming.',
    badge: 'Member rate',
    glow: 'radial-gradient(120% 120% at 70% 20%, #2bf6ff 0%, #0b6a7a 38%, #001214 70%)',
  },
  {
    id: 'p3',
    name: 'Keychron Q1 Pro',
    category: 'Desk',
    price: '₹17,499',
    source: 'Amazon',
    description: 'Gasket-mounted wireless mechanical keyboard with a CNC aluminium body.',
    glow: 'radial-gradient(120% 120% at 50% 15%, #c6ff3d 0%, #5a7a0b 38%, #0a1200 70%)',
  },
  {
    id: 'p4',
    name: 'Kindle Paperwhite',
    category: 'Read',
    price: '₹16,999',
    source: 'Flipkart',
    description: '6.8-inch glare-free display, weeks of battery, warm-light reading.',
    glow: 'radial-gradient(120% 120% at 35% 25%, #ffae2b 0%, #7a4a0b 38%, #120c00 70%)',
  },
  {
    id: 'p5',
    name: 'DJI Osmo Pocket 3',
    category: 'Capture',
    price: '₹71,900',
    source: 'Amazon',
    description: '1-inch sensor gimbal camera shooting stabilised 4K/120 in your palm.',
    badge: 'New',
    glow: 'radial-gradient(120% 120% at 60% 20%, #2b7bff 0%, #0b317a 38%, #000812 70%)',
  },
  {
    id: 'p6',
    name: 'Samsung T7 Shield 2TB',
    category: 'Storage',
    price: '₹13,499',
    source: 'Flipkart',
    description: 'Rugged, pocketable SSD with 1,050 MB/s transfers and IP65 protection.',
    glow: 'radial-gradient(120% 120% at 40% 20%, #b02bff 0%, #420b7a 38%, #0a0012 70%)',
  },
]
