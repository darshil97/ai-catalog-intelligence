import type { Metadata } from "next";
import { DM_Sans, Fraunces } from "next/font/google";
import type { ReactNode } from "react";

import { AccountProvider, AppHeader, SiteFooter } from "@/components/Account";
import "./globals.css";

const fraunces = Fraunces({ subsets: ["latin"], variable: "--font-fraunces", display: "swap" });
const dmSans = DM_Sans({ subsets: ["latin"], variable: "--font-dm-sans", display: "swap" });

export const metadata: Metadata = {
  title: "Catalog Intelligence",
  description:
    "Turn a supplier's spreadsheets, spec sheets and photos into a clean, checked product catalog.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${fraunces.variable} ${dmSans.variable}`}>
      <body>
        <AccountProvider>
          <AppHeader />
          {children}
          <SiteFooter />
        </AccountProvider>
      </body>
    </html>
  );
}
