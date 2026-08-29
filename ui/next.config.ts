import type { NextConfig } from 'next'

const API = process.env.QCIC_API_URL ?? 'http://localhost:8080'

const config: NextConfig = {
  // Same-origin proxy: keeps SSE off a cross-origin path, so no CORS
  // preflight and no credentialed-EventSource edge cases.
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${API}/api/:path*` }]
  },
}

export default config
