/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // Catalog refresh and two model calls can outlast Next's default 30-second proxy timeout.
  experimental: { proxyTimeout: 300_000 },
  async rewrites() {
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/:path*" }];
  },
};

export default nextConfig;
