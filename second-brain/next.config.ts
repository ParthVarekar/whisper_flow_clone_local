import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  typescript: {
    ignoreBuildErrors: true,
  },
  reactStrictMode: false,
  // Allow the sandbox gateway + localhost to access the dev server (Next.js 16
  // blocks cross-origin HMR by default).
  allowedDevOrigins: ["127.0.0.1", "localhost", "21.0.9.189"],
};

export default nextConfig;
