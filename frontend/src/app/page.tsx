"use client";

import Link from "next/link";

import { SignInButton, Waitlisted, useAccount } from "@/components/Account";
import { ApiStatus } from "@/components/ApiStatus";

export default function HomePage() {
  const account = useAccount();
  return (
    <main className="page">
      <h1>AI Catalog Intelligence</h1>
      <p className="lead">
        Turn messy supplier files into reviewed, export-ready product catalogs.
      </p>
      <div className="stack">
        {account.state === "loading" && <p className="muted">Checking your sign-in…</p>}
        {account.state === "signed_out" && <SignInButton />}
        {account.state === "waitlisted" && <Waitlisted />}
        {account.state === "active" && (
          <p>
            <Link href="/suppliers" className="button primary">
              Go to your suppliers
            </Link>
          </p>
        )}
        {(account.state === "denied" || account.state === "error") && (
          <p className="error" role="alert">
            {account.message}
          </p>
        )}
      </div>
      <ApiStatus />
    </main>
  );
}
