import type { NextConfig } from "next";

const config: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "Content-Security-Policy", value: "base-uri 'none'; frame-ancestors 'none'; form-action 'self'; object-src 'none'" },
        { key: "Referrer-Policy", value: "no-referrer" },
        { key: "X-Content-Type-Options", value: "nosniff" },
      ],
    }];
  },
};

export default config;
