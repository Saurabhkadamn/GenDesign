import type { NextConfig } from 'next';

const config: NextConfig = {
  transpilePackages: ['@forma/core'],
  async rewrites() {
    return process.env.FORMA_API_ORIGIN
      ? [{ source: '/api/:path*', destination: `${process.env.FORMA_API_ORIGIN}/api/:path*` }]
      : [];
  },
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'same-origin' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
        ],
      },
      { source: '/api/:path*', headers: [{ key: 'Cache-Control', value: 'private, no-store' }] },
    ];
  },
};
export default config;
