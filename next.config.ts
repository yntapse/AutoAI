import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: {
    root: "G:/ai agent saas/autoai-builder",
  },
  allowedDevOrigins: [
    "192.168.1.34",
    "172.24.48.1",
    "localhost",
    "127.0.0.1",
  ],
};

export default nextConfig;
