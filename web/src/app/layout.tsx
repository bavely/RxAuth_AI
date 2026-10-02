import type { Metadata } from "next";
import Link from "next/link";

import "./globals.css";

export const metadata: Metadata = {
  title: "RxAuth AI — Reviewer",
  description:
    "Administrative decision support for prior authorization. Prepares a case for a human reviewer; it does not decide, approve, deny, or submit one.",
  robots: { index: false, follow: false },
};

/**
 * The standing notice in the masthead is not decoration.
 *
 * README §1 and §20 are explicit that this system prepares a case and never
 * decides one, and the API's own OpenAPI description says so. A reviewer UI is
 * the first place that claim can quietly stop being true — a screen full of
 * green badges reads like an approval — so the boundary is stated on every
 * page rather than in documentation nobody has open.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="masthead">
          <div className="masthead-inner">
            <Link href="/" className="wordmark">
              RxAuth AI <span>Reviewer</span>
            </Link>
            <p className="standing-notice">
              Decision support. Nothing here is submitted, approved, or denied.
            </p>
          </div>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
