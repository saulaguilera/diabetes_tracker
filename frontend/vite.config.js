import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Orbit Copilot — frontend del producto.
// - base '/copilot/'  → Flask sirve el build bajo /copilot
// - outDir static/copilot → el build cae dentro de tu Flask (un solo deploy)
// - server.proxy → en desarrollo, /api lo reenvía a Flask (localhost:5050),
//   así la cookie de sesión funciona igual que en producción (mismo origen).
export default defineConfig({
  plugins: [react()],
  base: '/copilot/',
  build: {
    outDir: '../static/copilot',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:5050',
    },
  },
})
