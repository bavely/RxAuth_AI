import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // Ships a self-contained server with only the files it actually imports, so
  // the runtime image carries no build toolchain and no dev dependencies.
  output: "standalone",
  // The reviewer UI renders patient documents. None of it should be cached by
  // an intermediary, indexed, or sent to a third party as a referrer.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Robots-Tag", value: "noindex, nofollow" },
        ],
      },
    ];
  },
};

export default config;
