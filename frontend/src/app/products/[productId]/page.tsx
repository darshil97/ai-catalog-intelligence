"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { RequireActive } from "@/components/Account";
import { checkSummary } from "@/components/ChecksPanel";
import { ENRICHMENT_STATE_TEXT } from "@/components/EnrichmentPanel";
import { ApiError, api, jsonBody } from "@/lib/api";
import { describeLocation, FLAG_TEXT, VALUE_FLAG_TEXT } from "@/lib/location";
import type {
  CatalogValue,
  EnrichmentStarted,
  GeneratedClaim,
  GeneratedContent,
  Observation,
  ProductCategory,
  ProductValidation,
  ValidationIssue,
  ProductDetail,
} from "@/lib/types";
import { useApiResource } from "@/lib/useApiResource";

export default function ProductPage() {
  return (
    <main className="page">
      <RequireActive>
        <ProductView />
      </RequireActive>
    </main>
  );
}

function ProductView() {
  const { productId } = useParams<{ productId: string }>();
  const product = useApiResource<ProductDetail>(`/products/${productId}`);

  if (!product.data) {
    const message =
      product.error?.status === 404
        ? "This product doesn't exist, or isn't yours."
        : product.error?.message;
    return message ? (
      <p className="error" role="alert">
        {message}
      </p>
    ) : (
      <p className="muted">Loading…</p>
    );
  }

  const data = product.data;
  return (
    <div className="stack">
      <p>
        <Link href={`/batches/${data.batch_id}`}>← Batch</Link>
      </p>
      <div className="title-row">
        <h1>{data.product_key}</h1>
        <span className="badge">{data.status}</span>
      </div>
      <p className="muted">
        Product {data.source_order} in file order.
        {data.supplier_sku !== null &&
          data.supplier_sku !== data.product_key &&
          ` Written in the file as "${data.supplier_sku}".`}
      </p>
      {data.identifier_flags.map((flag) => (
        <p key={flag} className="error">
          {FLAG_TEXT[flag] ?? flag}
        </p>
      ))}
      <CategoryLine category={data.category} />
      {data.validation && (
        <ChecksSection
          batchId={data.batch_id}
          productId={data.id}
          checks={data.validation}
          onChange={product.reload}
        />
      )}
      <h2>Catalog values</h2>
      <p className="muted">
        Each value in standard units, shown only when every source agrees.
        Disagreements wait for you in the batch&apos;s review list.
      </p>
      <CatalogTable values={data.catalog_values} />
      {data.generated_content && (
        <GeneratedSection
          batchId={data.batch_id}
          productId={data.id}
          content={data.generated_content}
          onChange={product.reload}
        />
      )}
      {data.images.length > 0 && (
        <>
          <h2>Photos</h2>
          <ul className="photo-strip">
            {data.images.map((image) => (
              <li key={image.image_id}>
                {image.thumbnail_url ? (
                  // Signed links to private storage: next/image can't optimise them.
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={image.thumbnail_url} alt={image.image_name} />
                ) : (
                  <span className="thumb-missing">No preview</span>
                )}
                <span className="muted">
                  {image.image_name}
                  {image.decision === "accepted"
                    ? " · confirmed by you"
                    : " · its code is on it"}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      <h2>What the files say</h2>
      <p className="muted">
        Each value is exactly as written in the file. Where the files give more
        than one value for an attribute, every one is listed and marked
        &ldquo;differs&rdquo;; none is chosen here.
      </p>
      <ObservationTable
        observations={data.observations}
        differing={new Set(data.differing_attributes)}
      />
    </div>
  );
}

function ObservationTable({
  observations,
  differing,
}: {
  observations: Observation[];
  differing: Set<string>;
}) {
  if (observations.length === 0) {
    return <p className="muted">Nothing was read for this product.</p>;
  }
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Attribute</th>
            <th scope="col">Value</th>
            <th scope="col">From</th>
            <th scope="col">Read by</th>
          </tr>
        </thead>
        <tbody>
          {observations.map((observation) => (
            <tr key={observation.id}>
              <td>
                {observation.attribute_name}
                {differing.has(observation.attribute_code) && (
                  <>
                    {" "}
                    <span className="badge warning">differs</span>
                  </>
                )}
              </td>
              <td className="value">{observation.raw_value}</td>
              <td>
                {observation.source_file_name}
                <br />
                <span className="muted">
                  {describeLocation(observation.location)}
                  {observation.location.header
                    ? ` ("${observation.location.header}")`
                    : ""}
                  {observation.location.quote
                    ? `: “${observation.location.quote}”`
                    : ""}
                </span>
              </td>
              <td>
                {observation.location.quote
                  ? "LLM, checked against the text"
                  : observation.mapped_by === "llm"
                    ? `LLM, ${Math.round((observation.confidence ?? 0) * 100)}% sure`
                    : "Known name"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const STATE_TEXT: Record<CatalogValue["state"], string> = {
  verified: "sources agree",
  conflict: "sources disagree",
  unclear: "needs a person",
  missing: "missing",
  reviewer_set: "set by you",
};

function CategoryLine({ category }: { category: ProductCategory | null }) {
  if (!category || category.state === "none") return null;
  if (category.state === "needs_review") {
    return (
      <p className="muted">
        Category: waiting for you to choose
        {category.suggested ? ` (suggested: ${category.suggested.name})` : ""}.
      </p>
    );
  }
  return (
    <p>
      Category: <strong>{category.category?.name}</strong>{" "}
      <span className="muted">
        {category.source === "person"
          ? "· chosen by you"
          : `· chosen by the LLM, ${Math.round((category.confidence ?? 0) * 100)}% sure`}
      </span>
    </p>
  );
}

function CatalogTable({ values }: { values: CatalogValue[] }) {
  const shown = values.filter((value) => value.state !== "missing");
  const missing = values.filter((value) => value.state === "missing");
  return (
    <>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Attribute</th>
              <th scope="col">Value</th>
              <th scope="col">From the sources</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((value) => (
              <tr key={value.code}>
                <td>
                  {value.name}{" "}
                  <span
                    className={
                      value.state === "conflict" || value.state === "unclear"
                        ? "badge warning"
                        : "badge"
                    }
                  >
                    {STATE_TEXT[value.state]}
                  </span>
                </td>
                <td className="value">{value.shown ?? "—"}</td>
                <td>
                  <ul className="evidence">
                    {value.values.map((source) => (
                      <li key={source.id}>
                        {source.display} from &ldquo;{source.raw_value}&rdquo;,{" "}
                        {source.source_file_name}
                        {source.flags.length > 0 &&
                          ` (${source.flags.map((f) => VALUE_FLAG_TEXT[f] ?? f).join(", ")})`}
                      </li>
                    ))}
                  </ul>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {missing.length > 0 && (
        <p className="muted">
          No source gave: {missing.map((value) => value.name).join(", ")}.
        </p>
      )}
    </>
  );
}

function GeneratedSection({
  batchId,
  productId,
  content,
  onChange,
}: {
  batchId: string;
  productId: string;
  content: GeneratedContent;
  onChange: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const claims = (field: GeneratedClaim["field"]) =>
    content.claims.filter((claim) => claim.field === field);
  const title = claims("title")[0];

  async function regenerate() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await api<EnrichmentStarted>(
        `/batches/${batchId}/enrichment`,
        jsonBody("POST", { product_ids: [productId] }),
      );
      setMessage("Writing now. Reload in a minute to see it.");
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Couldn't start writing.",
      );
    } finally {
      setBusy(false);
      onChange();
    }
  }

  const warn = content.state === "failed" || content.state === "out_of_date";
  return (
    <section className="stack">
      <div className="title-row">
        <h2>Generated content</h2>
        <span className={`badge${warn ? " warning" : ""}`}>
          {ENRICHMENT_STATE_TEXT[content.state]}
        </span>
      </div>
      {content.message && <p className="muted">{content.message}</p>}
      {content.version !== null && title && (
        <div className="stack">
          <ClaimLine claim={title} strong />
          {claims("description").map((claim) => (
            <ClaimLine key={`d${claim.index}`} claim={claim} />
          ))}
          <ul className="claims">
            {claims("bullets").map((claim) => (
              <li key={`b${claim.index}`}>
                <ClaimLine claim={claim} />
              </li>
            ))}
          </ul>
          <p className="muted">
            Version {content.version}
            {content.model_id && ` · written by ${content.model_id}`}
            {content.rules_version &&
              ` · checked with ${content.rules_version}`}
          </p>
        </div>
      )}
      {content.can_generate && (
        <div className="actions">
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={regenerate}
          >
            {busy
              ? "Starting…"
              : content.version === null
                ? "Generate"
                : "Regenerate"}
          </button>
        </div>
      )}
      {message && <p className="muted">{message}</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {content.left_out.length > 0 && (
        <>
          <h3>Left out</h3>
          <ul className="list">
            {content.left_out.map((item) => (
              <li key={item.code}>
                <span>{item.name}</span>
                <span className="muted">{item.reason}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function ClaimLine({
  claim,
  strong = false,
}: {
  claim: GeneratedClaim;
  strong?: boolean;
}) {
  return (
    <p className="claim">
      {strong ? <strong>{claim.text}</strong> : claim.text}{" "}
      {claim.facts.map((fact) => (
        <span
          key={`${fact.code}-${fact.display}`}
          className="chip"
          title={`${fact.name}: ${fact.display} (${fact.state})`}
        >
          {fact.name}{" "}
          {fact.display.length > 40
            ? `${fact.display.slice(0, 37)}…`
            : fact.display}
        </span>
      ))}
    </p>
  );
}

const SEVERITY_TEXT: Record<ValidationIssue["severity"], string> = {
  error: "Error",
  warning: "Warning",
  info: "Note",
};

const FIX_TEXT: Record<NonNullable<ValidationIssue["fix"]>, string> = {
  review: "Fix in the batch's review list",
  generate: "Generate text on the batch page",
  photos: "Confirm a photo on the batch page",
};

function ChecksSection({
  batchId,
  productId,
  checks,
  onChange,
}: {
  batchId: string;
  productId: string;
  checks: ProductValidation;
  onChange: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const key = (issue: ValidationIssue) => `${issue.rule}:${issue.subject}`;

  async function send(path: string, init: RequestInit) {
    setError(null);
    try {
      await api<ProductValidation>(path, init);
      setAsking(null);
      setReason("");
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Couldn't save that.",
      );
    } finally {
      onChange();
    }
  }

  const base = `/products/${productId}/validation/set-aside`;
  return (
    <section className="stack">
      <div className="title-row">
        <h2>Checks for {checks.profile_name}</h2>
        <span className={`badge${checks.errors ? " warning" : ""}`}>
          {checkSummary(checks)}
        </span>
      </div>
      {checks.issues.length === 0 && <p className="muted">Nothing to fix.</p>}
      <ul className="list">
        {checks.issues.map((issue) => (
          <li
            key={key(issue)}
            className={issue.set_aside ? "muted" : undefined}
          >
            <span>
              <span
                className={`badge${issue.severity === "error" ? " warning" : ""}`}
              >
                {SEVERITY_TEXT[issue.severity]}
              </span>{" "}
              {issue.message}
            </span>
            <span className="muted">
              {issue.other_product_id && (
                <>
                  <Link href={`/products/${issue.other_product_id}`}>
                    Open the other product
                  </Link>
                  {" · "}
                </>
              )}
              {issue.fix && !issue.set_aside && (
                <Link href={`/batches/${batchId}`}>{FIX_TEXT[issue.fix]}</Link>
              )}
              {issue.set_aside && `Set aside: ${issue.set_aside.reason}`}
            </span>
            {issue.severity === "warning" && (
              <span className="actions">
                {issue.set_aside ? (
                  <button
                    type="button"
                    className="button link"
                    onClick={() =>
                      send(
                        `${base}?rule=${encodeURIComponent(issue.rule)}&subject=${encodeURIComponent(issue.subject)}`,
                        { method: "DELETE" },
                      )
                    }
                  >
                    Undo
                  </button>
                ) : asking === key(issue) ? (
                  <form
                    className="confirm"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void send(
                        base,
                        jsonBody("POST", {
                          rule: issue.rule,
                          subject: issue.subject,
                          reason,
                        }),
                      );
                    }}
                  >
                    <input
                      value={reason}
                      maxLength={500}
                      required
                      placeholder="Why it's fine"
                      aria-label="Reason"
                      onChange={(event) => setReason(event.target.value)}
                    />
                    <button
                      type="submit"
                      className="button"
                      disabled={!reason.trim()}
                    >
                      Set aside
                    </button>
                    <button
                      type="button"
                      className="button link"
                      onClick={() => setAsking(null)}
                    >
                      Cancel
                    </button>
                  </form>
                ) : (
                  <button
                    type="button"
                    className="button link"
                    onClick={() => {
                      setAsking(key(issue));
                      setReason("");
                    }}
                  >
                    Set aside
                  </button>
                )}
              </span>
            )}
          </li>
        ))}
      </ul>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
