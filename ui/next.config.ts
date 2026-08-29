import type { NextConfig } from 'next'

const API = process.env.QCIC_API_URL ?? 'http://localhost:8080'

// Static export: every page here is a client component, so there is nothing to
// render on a server. Exporting lets FastAPI serve the UI and the API from one
// origin - no second service, no proxy, no CORS, and no cross-origin EventSource.
const config: NextConfig = {
  output: 'export',
  distDir: 'dist',
  images: { unoptimized: true },
  ...(process.env.NODE_ENV === 'development'
    ? { rewrites: async () => [{ source: '/api/:path*', destination: `${API}/api/:path*` }] }
    : {}),
}

export default config
