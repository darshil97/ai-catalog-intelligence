"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { ApiError, api, clearApiToken } from "@/lib/api";
import { authClient } from "@/lib/auth/client";
import type { Me } from "@/lib/types";

type AccountState =
  | { state: "loading" }
  | { state: "signed_out" }
  | { state: "waitlisted"; me: Me }
  | { state: "active"; me: Me }
  | { state: "denied"; message: string }
  | { state: "error"; message: string };

const AccountContext = createContext<AccountState>({ state: "loading" });

/** Loads the Neon Auth session, then asks the API whether this account may use the app. */
export function AccountProvider({ children }: { children: ReactNode }) {
  const session = authClient.useSession();
  const userId = session.data?.user?.id;
  // The API's answer for one user. Kept with the user ID, so a different user never sees it.
  const [result, setResult] = useState<{ userId: string; account: AccountState } | null>(null);

  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    const settle = (account: AccountState) => {
      if (!cancelled) setResult({ userId, account });
    };
    api<Me>("/me")
      .then((me) =>
        settle(me.status === "active" ? { state: "active", me } : { state: "waitlisted", me }),
      )
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 401) settle({ state: "signed_out" });
        else if (error instanceof ApiError && error.status === 403)
          settle({ state: "denied", message: error.message });
        else
          settle({
            state: "error",
            message: "The API isn't available right now. Try again in a minute.",
          });
      });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  let account: AccountState;
  if (session.isPending) account = { state: "loading" };
  else if (!userId) account = { state: "signed_out" };
  else if (result?.userId === userId) account = result.account;
  else account = { state: "loading" };

  return <AccountContext.Provider value={account}>{children}</AccountContext.Provider>;
}

export function useAccount(): AccountState {
  return useContext(AccountContext);
}

export function SignInButton({ label = "Sign in with Google", large = false }: {
  label?: string;
  large?: boolean;
}) {
  const [pending, setPending] = useState(false);
  const signIn = useCallback(async () => {
    setPending(true);
    try {
      await authClient.signIn.social({ provider: "google", callbackURL: "/suppliers" });
    } finally {
      setPending(false);
    }
  }, []);
  return (
    <button
      type="button"
      className={`button primary${large ? " large" : ""}`}
      onClick={signIn}
      disabled={pending}
    >
      <span className="google" aria-hidden="true">
        G
      </span>
      {pending ? "Redirecting to Google…" : label}
    </button>
  );
}

export function SignOutButton() {
  const router = useRouter();
  const signOut = useCallback(async () => {
    await authClient.signOut();
    clearApiToken();
    router.push("/");
  }, [router]);
  return (
    <button type="button" className="button link" onClick={signOut}>
      Sign out
    </button>
  );
}

const APP_LINKS = [
  { href: "/suppliers", label: "Suppliers", match: ["/suppliers", "/batches", "/products"] },
  { href: "/how-it-works", label: "How it works", match: ["/how-it-works"] },
];

/** Top bar: the brand, the app's pages once signed in, and the account. */
export function AppHeader() {
  const account = useAccount();
  const pathname = usePathname();
  const email =
    account.state === "active" || account.state === "waitlisted" ? account.me.email : null;
  const active = account.state === "active";
  return (
    <header className="app-header">
      <Link href="/" className="brand">
        <span className="logo" aria-hidden="true">
          CI
        </span>
        Catalog Intelligence
      </Link>
      {active ? (
        <nav className="nav-pill" aria-label="Main">
          {APP_LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={link.match.some((m) => pathname.startsWith(m)) ? "active" : undefined}
              aria-current={link.match.some((m) => pathname.startsWith(m)) ? "page" : undefined}
            >
              {link.label}
            </Link>
          ))}
        </nav>
      ) : (
        <nav className="account" aria-label="Main">
          <Link href="/how-it-works" className="muted">
            How it works
          </Link>
        </nav>
      )}
      {email ? (
        <span className="account">
          <span className="avatar" aria-hidden="true">
            {email.slice(0, 2)}
          </span>
          <span className="muted email">{email}</span>
          <SignOutButton />
        </span>
      ) : (
        account.state === "signed_out" && <SignInButton label="Sign in" />
      )}
    </header>
  );
}

/** Footer on every page. */
export function SiteFooter() {
  return (
    <footer className="site-foot">
      <span>Catalog Intelligence · a free preview · Home &amp; Kitchen demo category</span>
      <span className="row">
        <Link href="/how-it-works">How it works</Link>
        <Link href="/">Home</Link>
      </span>
    </footer>
  );
}

/** Renders children only for active accounts, and the right message for everyone else. */
export function RequireActive({ children }: { children: ReactNode }) {
  const account = useAccount();
  switch (account.state) {
    case "loading":
      return (
        <p className="muted">Loading your account. The first request can take up to a minute.</p>
      );
    case "signed_out":
      return (
        <div className="notice tinted">
          <h2>Sign in to continue</h2>
          <p className="muted">Sign in with Google to manage your suppliers and batches.</p>
          <div>
            <SignInButton />
          </div>
        </div>
      );
    case "waitlisted":
      return <Waitlisted />;
    case "denied":
    case "error":
      return (
        <p className="error" role="alert">
          {account.message}
        </p>
      );
    case "active":
      return <>{children}</>;
  }
}

export function Waitlisted() {
  return (
    <div className="notice tinted" role="status">
      <h2>You&apos;re on the waitlist</h2>
      <p>
        Catalog Intelligence is in a small preview. We&apos;ll let you in when a place opens up.
        You don&apos;t need to do anything else.
      </p>
      <div>
        <SignOutButton />
      </div>
    </div>
  );
}
