// One-off E2E driver for the "Talk to your product" feature. Drives the real app
// on :5000: search -> click Talk -> wait for ready -> ask -> screenshot each stage.
import { chromium } from 'playwright'

const SHOTS = process.env.TEMP + '\\talk'
const log = (...a) => console.log(...a)

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

await page.goto('http://localhost:5000', { waitUntil: 'networkidle' })

// Run a concierge search to get product cards.
await page.locator('.concierge-field textarea').fill('a flagship Samsung phone with a great camera and S Pen')
await page.getByRole('button', { name: /Curate/i }).click()
log('search submitted, waiting for cards...')
await page.locator('.product-grid .card').first().waitFor({ timeout: 90000 })
const cardCount = await page.locator('.product-grid .card').count()
log('cards:', cardCount)
await page.locator('.product-grid').scrollIntoViewIfNeeded()
await page.screenshot({ path: `${SHOTS}-1-cards.png` })

// Open the talk drawer on the first product.
await page.locator('.card-talk').first().click()
await page.waitForTimeout(1500)
await page.screenshot({ path: `${SHOTS}-2-preparing.png` })
log('drawer opened (preparing)')

// Wait until the input is enabled (phase === ready).
await page.locator('.talk-input textarea:not([disabled])').waitFor({ timeout: 120000 })
await page.waitForTimeout(800)
await page.screenshot({ path: `${SHOTS}-3-ready.png` })
log('ready')

// Ask a question via a suggestion chip.
await page.locator('.talk-suggestions .chip').first().click()
await page.locator('.bubble.assistant .bubble-text').first().waitFor({ timeout: 60000 })
await page.waitForTimeout(1000)
await page.screenshot({ path: `${SHOTS}-4-answer.png` })
const answer = await page.locator('.bubble.assistant .bubble-text').first().innerText()
const quota = await page.locator('.quota-bar').innerText().catch(() => 'n/a')
log('QUOTA BAR:', quota)
log('ANSWER (first 200):', answer.slice(0, 200))

await browser.close()
log('done')
