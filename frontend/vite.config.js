import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // dev: proxy to FastAPI so no CORS setup is needed locally
  server: { proxy: { '/api': 'http://localhost:8000' } },
})
