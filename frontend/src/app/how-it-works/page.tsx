import type { Metadata } from "next";
import Link from "next/link";

import { LIMITS, STEPS } from "@/lib/content";

export const metadata: Metadata = {
  title: "How it works · Catalog Intelligence",
  description: "Six steps from a supplier's files to a marketplace-ready catalog.",
};

export default function HowItWorksPage() {
  return (
    <main className="hero-bg">
      <div className="wrap">
        <div className="doc">
          <nav className="doc-toc" aria-label="On this page">
            <strong>On this page</strong>
            {STEPS.map((step, index) => (
              <a key={step.id} href={`#${step.id}`}>
                {index + 1}. {step.title}
              </a>
            ))}
            <a href="#limits">Limits during the preview</a>
          </nav>
          <div className="doc-body">
            <p className="eyebrow">Guide</p>
            <h1>How to use Catalog Intelligence</h1>
            <p className="lead" style={{ marginTop: "1rem" }}>
              Six steps from a supplier&apos;s files to a marketplace-ready catalog. Most of the
              work is automatic; you only decide what the system isn&apos;t sure about.
            </p>
            <ol className="how-list">
              {STEPS.map((step, index) => (
                <li key={step.id} id={step.id} className="how-step">
                  <span className="how-n" aria-hidden="true">
                    {index + 1}
                  </span>
                  <div>
                    <h2 style={{ fontSize: "1.1rem" }}>{step.title}</h2>
                    <p>{step.text}</p>
                    <p className="tip">💡 {step.tip}</p>
                  </div>
                </li>
              ))}
            </ol>
            <section id="limits" className="callout">
              <h2 style={{ fontSize: "1.1rem" }}>Limits during the preview</h2>
              <ul>
                {LIMITS.map((limit) => (
                  <li key={limit}>{limit}</li>
                ))}
              </ul>
            </section>
            <p style={{ marginTop: "2rem" }}>
              <Link href="/suppliers" className="button primary">
                Start with your first supplier →
              </Link>
            </p>
          </div>
        </div>
      </div>
    </main>
  );
}
