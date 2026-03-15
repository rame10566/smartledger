import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Dashboard API runs on port 8000 in dev
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
