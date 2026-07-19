/** @type {import('next').NextConfig} */
module.exports = {
  reactStrictMode: true,

  /* Self-host Google Fonts via next/font — eliminates external round-trip */
  optimizeFonts: true,

  /* Compress responses */
  compress: true,

  /* Remove X-Powered-By header */
  poweredByHeader: false,

  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000',
    NEXT_PUBLIC_WS_URL:  process.env.NEXT_PUBLIC_WS_URL  ?? 'ws://localhost:8000',
  },

  /* Custom HTTP headers for security + performance */
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Content-Type-Options',    value: 'nosniff'         },
          { key: 'X-Frame-Options',            value: 'DENY'            },
          { key: 'Referrer-Policy',            value: 'strict-origin'   },
          {
            key: 'Content-Security-Policy',
            value: [
              "default-src 'self'",
              "script-src 'self' 'unsafe-eval' 'unsafe-inline'",
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data: blob:",
              "connect-src 'self' ws: wss: http://localhost:8000 ws://localhost:8000 http://*.local:8000 ws://*.local:8000 http://10.*:8000 ws://10.*:8000 http://192.168.*:8000 ws://192.168.*:8000 http://172.*:8000 ws://172.*:8000",
              "font-src 'self' data:",
              "frame-ancestors 'none'",
            ].join('; '),
          },
        ],
      },
    ]
  },
}