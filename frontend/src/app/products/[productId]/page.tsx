"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { RequireActive } from "@/components/Account";
import { checkSummary } from "@/components/ChecksPanel";
import { ConfirmButton } from "@/components/ConfirmButton";
import {
  ApprovalBar,
  CopyEditor,
  DuplicateActions,
  HistorySection,
  ValueEditor,
} from "@/components/ProductReview";
import { ENRICHMENT_STATE_TEXT } from "@/components/EnrichmentPanel";
import { Working } from "@/components/Working";
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
  const [editing, setEditing] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const reload = () => {
    product.reload();
    setVersion((current) => current + 1);
  };

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
  const revision = data.approval?.revision ?? 0;
  function edit(code: string) {
    setEditing(code);
    document
      .getElementById(`value-${code}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  return (
    <div className="stack">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link href="/suppliers">Suppliers</Link>
        <span aria-hidden="true">/</span>
        <Link href={`/batches/${data.batch_id}`}>Batch</Link>
        <span aria-hidden="true">/</span>
        <span>{data.product_key}</span>
      </nav>
      <div className="page-head">
        <div>
          <h1>{data.product_key}</h1>
          <p className="sub">
            Product {data.source_order} in file order
            {data.supplier_sku !== null &&
              data.supplier_sku !== data.product_key &&
              ` · written in the file as “${data.supplier_sku}”`}
          </p>
        </div>
        {data.approval && (
          <ApprovalBar
            productId={data.id}
            approval={data.approval}
            onChanged={reload}
          />
        )}
      </div>
      {data.identifier_flags.map((flag) => (
        <p key={flag} className="error">
          {FLAG_TEXT[flag] ?? flag}
        </p>
      ))}
      <CategoryLine category={data.category} />
      <div className="grid-2">
        <div className="stack">
          {data.generated_content && (
            <GeneratedSection
              batchId={data.batch_id}
              productId={data.id}
              content={data.generated_content}
              revision={revision}
              onChange={reload}
            />
          )}
          <section className="section">
            <h2>Catalog values</h2>
            <p className="muted">
              Each value in standard units, shown only when every source agrees.
              Disagreements wait for you in the batch&apos;s review list.
            </p>
            <CatalogTable
              productId={data.id}
              values={data.catalog_values}
              revision={revision}
              editing={editing}
              onEdit={setEditing}
              onSaved={reload}
            />
          </section>
          <section className="section">
            <h2>What the files say</h2>
            <p className="muted">
              Each value exactly as written in the file. Where the files give
              more than one value for an attribute, every one is listed and
              marked &ldquo;differs&rdquo;; none is chosen here.
            </p>
            <ObservationTable
              observations={data.observations}
              differing={new Set(data.differing_attributes)}
            />
          </section>
        </div>
        <div className="stack">
          {data.validation && (
            <ChecksSection
              batchId={data.batch_id}
              productId={data.id}
              checks={data.validation}
              revision={revision}
              onEdit={edit}
              onChange={reload}
            />
          )}
          <section className="section">
            <h2>Photos</h2>
            {data.images.length === 0 ? (
              <p className="muted">
                No photo is confirmed for this product yet.
              </p>
            ) : (
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
            )}
          </section>
          <HistorySection productId={data.id} version={version} />
        </div>
      </div>
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

function CatalogTable({
  productId,
  values,
  revision,
  editing,
  onEdit,
  onSaved,
}: {
  productId: string;
  values: CatalogValue[];
  revision: number;
  editing: string | null;
  onEdit: (code: string | null) => void;
  onSaved: () => void;
}) {
  // Missing values come last, so they can be filled in too.
  const shown = [
    ...values.filter((value) => value.state !== "missing"),
    ...values.filter(
      (value) => value.state === "missing" && value.code !== "sku",
    ),
  ];
  const missing: CatalogValue[] = [];
  return (
    <>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Attribute</th>
              <th scope="col">Value</th>
              <th scope="col">From the sources</th>
              <th scope="col">
                <span className="visually-hidden">Edit</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((value) => (
              <tr key={value.code} id={`value-${value.code}`}>
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
                  {editing === value.code && (
                    <ValueEditor
                      productId={productId}
                      code={value.code}
                      name={value.name}
                      current={value.shown}
                      revision={revision}
                      onDone={(saved) => {
                        onEdit(null);
                        if (saved) onSaved();
                      }}
                    />
                  )}
                </td>
                <td>
                  {value.code !== "sku" && editing !== value.code && (
                    <button
                      type="button"
                      className="button link"
                      onClick={() => onEdit(value.code)}
                    >
                      {value.shown ? "Edit" : "Add"}
                    </button>
                  )}
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
  revision,
  onChange,
}: {
  batchId: string;
  productId: string;
  content: GeneratedContent;
  revision: number;
  onChange: () => void;
}) {
  const [editingCopy, setEditingCopy] = useState(false);
  const [busy, setBusy] = useState(false);
  // The copy version when writing started; cleared once a new version arrives.
  const [writingFrom, setWritingFrom] = useState<number | null | undefined>(
    undefined,
  );
  const [error, setError] = useState<string | null>(null);
  // Writing until a new version arrives or writing fails.
  const writing =
    writingFrom !== undefined &&
    content.version === writingFrom &&
    content.state !== "failed";
  useEffect(() => {
    if (!writing) return;
    const started = Date.now();
    const timer = window.setInterval(() => {
      // Copy takes seconds; stop asking after three minutes.
      if (Date.now() - started > 180_000) setWritingFrom(undefined);
      else onChange();
    }, 4000);
    return () => window.clearInterval(timer);
  }, [writing, onChange]);
  const claims = (field: GeneratedClaim["field"]) =>
    content.claims.filter((claim) => claim.field === field);
  const title = claims("title")[0];

  async function regenerate() {
    setBusy(true);
    setError(null);
    try {
      await api<EnrichmentStarted>(
        `/batches/${batchId}/enrichment`,
        jsonBody("POST", { product_ids: [productId] }),
      );
      setWritingFrom(content.version);
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
    <section className="section">
      <div className="title-row">
        <h2>Generated content</h2>
        <span className={`badge${warn ? " warning" : ""}`}>
          {ENRICHMENT_STATE_TEXT[content.state]}
        </span>
      </div>
      {content.message && <p className="muted">{content.message}</p>}
      {content.reasons.length > 0 && content.state !== "failed" && (
        <div className="notice tinted">
          <strong>
            {content.state === "out_of_date"
              ? "Why it needs rewriting"
              : "Why there's no copy yet"}
          </strong>
          <ul className="reasons">
            {content.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}
      {editingCopy && (
        <CopyEditor
          productId={productId}
          content={content}
          revision={revision}
          onDone={(saved) => {
            setEditingCopy(false);
            if (saved) onChange();
          }}
        />
      )}
      {!editingCopy && content.edited && content.title && (
        <div className="stack">
          <p className="claim">
            <strong>{content.title}</strong>
          </p>
          {content.description && (
            <p className="claim">{content.description}</p>
          )}
          <ul className="claims">
            {content.bullets.map((bullet) => (
              <li key={bullet}>{bullet}</li>
            ))}
          </ul>
          <p className="muted">Version {content.version} · written by you</p>
        </div>
      )}
      {!editingCopy && !content.edited && content.version !== null && title && (
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
      {!editingCopy && (
        <div className="actions">
          {content.can_generate && !content.edited && (
            <button
              type="button"
              className="button"
              disabled={busy || writing}
              aria-busy={busy || undefined}
              onClick={regenerate}
            >
              {busy
                ? "Starting…"
                : content.version === null
                  ? "Generate"
                  : "Regenerate"}
            </button>
          )}
          {content.can_generate && content.edited && (
            <ConfirmButton
              className="button"
              label="Regenerate"
              question="This replaces the copy you wrote. Regenerate?"
              confirmLabel="Replace my copy"
              onConfirm={regenerate}
            />
          )}
          <button
            type="button"
            className="button"
            onClick={() => setEditingCopy(true)}
          >
            {content.version === null ? "Write it myself" : "Edit"}
          </button>
        </div>
      )}
      {writing && <Working label="Writing copy…" compact />}
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
  copy: "Edit the copy",
  review: "Fix in the batch's review list",
  generate: "Generate text on the batch page",
  photos: "Confirm a photo on the batch page",
};

function ChecksSection({
  batchId,
  productId,
  checks,
  revision,
  onEdit,
  onChange,
}: {
  batchId: string;
  productId: string;
  checks: ProductValidation;
  revision: number;
  onEdit: (code: string) => void;
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
    <section className="section">
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
              {issue.attribute && !issue.set_aside && (
                <button
                  type="button"
                  className="button link"
                  onClick={() => onEdit(issue.attribute as string)}
                >
                  Edit {issue.attribute.replace(/_/g, " ")}
                </button>
              )}
              {issue.attribute && issue.fix && !issue.set_aside && " · "}
              {issue.fix && issue.fix !== "copy" && !issue.set_aside && (
                <Link href={`/batches/${batchId}`}>{FIX_TEXT[issue.fix]}</Link>
              )}
              {issue.set_aside && `Set aside: ${issue.set_aside.reason}`}
            </span>
            {!issue.set_aside && (
              <DuplicateActions
                productId={productId}
                issue={issue}
                revision={revision}
                onChanged={onChange}
              />
            )}
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
