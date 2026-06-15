export default function Footer() {
  return (
    <footer className="footer">
      <div className="wrap">
        <div className="footer-bottom">
          <span>© {new Date().getFullYear()} Obsidian. Crafted for the few.</span>
          <span>Privacy · Terms · Cookies</span>
        </div>
      </div>
    </footer>
  )
}
