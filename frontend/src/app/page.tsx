"use client";

import Link from "next/link";

import { SignInButton, Waitlisted, useAccount } from "@/components/Account";
import { ApiStatus } from "@/components/ApiStatus";
import { FEATURES, PIPELINE } from "@/lib/content";

export default function HomePage() {
  return (
    <main className="hero-bg">
      <div className="wrap">
        <section className="hero">
          <div>
            <p className="eyebrow">Supplier catalog onboarding</p>
            <h1>
              Messy supplier files in.
              <br />
              <em>A clean, checked catalog out.</em>
            </h1>
            <p className="lead">
              Upload a supplier&apos;s spreadsheets, spec sheets and photos. Get structured
              products with every value traced to its source, photos matched to the right SKU,
              and listing copy that never invents a spec.
            </p>
            <Start />
          </div>
          <TraceCard />
        </section>

        <section className="band" aria-labelledby="steps">
          <h2 id="steps">From upload to export, in eight steps</h2>
          <ol className="pipeline">
            {PIPELINE.map((step, index) => (
              <li key={step.name}>
                <span className="step-n">{index + 1}</span>
                <strong>{step.name}</strong>
                <span>{step.text}</span>
              </li>
            ))}
          </ol>
        </section>

        <section className="features" aria-label="What it does">
          {FEATURES.map((feature) => (
            <article key={feature.title} className="feature">
              <span className="feature-icon" aria-hidden="true">
                {feature.icon}
              </span>
              <h3>{feature.title}</h3>
              <p>{feature.text}</p>
            </article>
          ))}
        </section>

        <section className="cta">
          <h2>Try it with your own supplier files</h2>
          <p>
            Sign in with Google to start. While the preview is small, new accounts join a short
            waitlist.
          </p>
          <Start compact />
        </section>
        <ApiStatus />
      </div>
    </main>
  );
}

/** What to do next, depending on whether you're signed in. */
function Start({ compact = false }: { compact?: boolean }) {
  const account = useAccount();
  if (account.state === "active") {
    return (
      <div className={compact ? "row" : "hero-actions"} style={compact ? { justifyContent: "center" } : undefined}>
        <Link href="/suppliers" className="button primary large">
          Go to your suppliers →
        </Link>
        {!compact && (
          <Link href="/how-it-works" className="button large">
            How it works
          </Link>
        )}
      </div>
    );
  }
  if (account.state === "waitlisted") {
    return compact ? null : (
      <div className="hero-actions">
        <Waitlisted />
      </div>
    );
  }
  return (
    <>
      <div className={compact ? "row" : "hero-actions"} style={compact ? { justifyContent: "center" } : undefined}>
        <SignInButton large />
        {!compact && (
          <Link href="/how-it-works" className="button large">
            See how it works →
          </Link>
        )}
      </div>
      {!compact && account.state === "signed_out" && (
        <p className="fine">Free preview · Google sign-in · New accounts join a short waitlist</p>
      )}
      {(account.state === "denied" || account.state === "error") && (
        <p className="error" role="alert">
          {account.message}
        </p>
      )}
    </>
  );
}

/** An example of a value traced to its sources, as the product page shows it. */
function TraceCard() {
  return (
    <figure className="trace-card" aria-label="Example: a value traced to its sources">
      <div className="trace-head">
        <span className="badge ok">Verified</span>
        <strong>Capacity</strong>
        <span className="big">1.5 L</span>
      </div>
      <ol className="trace">
        <li>
          <strong>prices.csv</strong> sheet 1 · row 7 · column D <code>1.5 L</code>
        </li>
        <li>
          <strong>specs.pdf</strong> page 2 · “Capacity: 1500 ml” <code>1500 ml</code>
        </li>
        <li>
          <strong>box-side.jpg</strong> text on the photo <code>1.5 Ltr</code>
        </li>
      </ol>
      <p className="cite">
        “Holds 1.5 litres” <span className="chip">Capacity 1.5 L</span>
      </p>
      <figcaption className="fine">
        Three sources agree, so it&apos;s shown. If one disagreed, you&apos;d decide.
      </figcaption>
    </figure>
  );
}
