import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AccountProvider, AppHeader } from "@/components/Account";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Catalog Intelligence",
  description: "Turn messy supplier files into reviewed, export-ready product catalogs.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AccountProvider>
          <AppHeader />
          {children}
        </AccountProvider>
      </body>
    </html>
  );
}
