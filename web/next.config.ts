import type { NextConfig } from "next";

const isProduction = process.env.NODE_ENV === "production";

// A strict CSP needs a per-request nonce for Next's inline bootstrap script, so
// it is deliberately left out here rather than weakened with 'unsafe-inline'.
// These headers are safe for every page and close the framing/sniffing gaps.
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  ...(isProduction
    ? [{ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" }]
    : []),
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  turbopack: {
    root: process.cwd(),
  },
  experimental: {
    optimizePackageImports: ["@adem.rejeb/ui", "lucide-react"],
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  // Browser-facing OAuth endpoints live on the console host, the only origin
  // that holds the session cookie. See app/api/oidc/discovery/route.ts.
  async rewrites() {
    return [
      { source: "/.well-known/openid-configuration", destination: "/api/oidc/discovery" },
      { source: "/.well-known/oauth-authorization-server", destination: "/api/oidc/discovery" },
      { source: "/v1/:path*", destination: "/api/proxy/v1/:path*" },
    ];
  },
};

export default nextConfig;
