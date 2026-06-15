import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Standalone dev server for the storefront UI. The /search proxy lets the React
// app talk to the existing Flask backend during local development without CORS setup.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/search': 'http://localhost:5000',
      '/talk': 'http://localhost:5000',
      '/watch': 'http://localhost:5000',
    },
  },
})
