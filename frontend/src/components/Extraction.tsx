"use client";

import Link from "next/link";

import { useEffect, useState } from "react";

import { ApprovalPanel } from "@/components/ApprovalPanel";
import { ImageGrid } from "@/components/ImageGrid";
import { NextStep } from "@/components/NextStep";
import { ChecksPanel } from "@/components/ChecksPanel";
import { EnrichmentPanel } from "@/components/EnrichmentPanel";
import { ExportPanel } from "@/components/ExportPanel";
import { ReviewPanel } from "@/components/ReviewPanel";
import { describeLocation, FLAG_TEXT } from "@/lib/location";
import type {
  BatchApproval,
  BatchEnrichment,
  BatchValidation,
  ReviewTaskList,
  BatchRun,
  DocumentFile,
  DocumentList,
  ProductList,
  RunError,
  ExportList,
  VariantDecided,
  VariantList,
  VariantPair,
  Sheet,
  SheetList,
} from "@/lib/types";
import { ApiError, api, jsonBody } from "@/lib/api";
import { useApiResource } from "@/lib/useApiResource";

type Tab =
  "products" | "review" | "copy" | "checks" | "export" | "photos" | "sources";

const MAPPING_TEXT: Record<Sheet["mapping_source"], string> = {
  none: "",
  known_names: "Columns recognised by name.",
  saved: "Columns mapped as last time for this supplier.",
  llm: "Columns the names didn't settle were mapped by the LLM.",
  llm_failed: "",
  llm_capped: "",
  llm_budget: "",
};

/**
 * What a finished run found: products, how each spreadsheet, PDF and photo was read, and rows,
 * pages or values that couldn't be used. Mounted once the run has finished, so it loads the
 * final result.
 */
/** How far a processed batch has got, for the progress strip at the top of the page. */
export type BatchProgress = {
  // Nothing left to review, write or fix: every product not rejected is ready or approved.
  reviewed: boolean;
  // Every product not rejected is approved, and the latest export holds them all.
  exported: boolean;
};

export function Extraction({
  batchId,
  run,
  onProgress,
}: {
  batchId: string;
  run: BatchRun;
  onProgress?: (progress: BatchProgress) => void;
}) {
  const products = useApiResource<ProductList>(`/batches/${batchId}/products`);
  const [version, setVersion] = useState(0);
  const [tab, setTab] = useState<Tab>("products");
  // What the next-step card and the tab counts need. Reloaded after any change and tab switch.
  const reviews = useApiResource<ReviewTaskList>(
    `/batches/${batchId}/review-tasks`,
  );
  const enrichment = useApiResource<BatchEnrichment>(
    `/batches/${batchId}/enrichment`,
  );
  const checks = useApiResource<BatchValidation>(
    `/batches/${batchId}/validation`,
  );
  const approval = useApiResource<BatchApproval>(
    `/batches/${batchId}/approval`,
  );
  const exports = useApiResource<ExportList>(`/batches/${batchId}/exports`);
  const { reload: reloadApproval } = approval;
  const { reload: reloadExports } = exports;
  const { reload: reloadReviews } = reviews;
  const { reload: reloadEnrichment } = enrichment;
  const { reload: reloadChecks } = checks;
  useEffect(() => {
    reloadReviews();
    reloadEnrichment();
    reloadChecks();
    reloadApproval();
    reloadExports();
  }, [
    tab,
    version,
    reloadReviews,
    reloadEnrichment,
    reloadChecks,
    reloadApproval,
    reloadExports,
  ]);
  const kept = approval.data?.items.filter((item) => item.state !== "rejected");
  const reviewed =
    reviews.data !== null &&
    reviews.data.items.every((task) => task.status !== "open") &&
    kept !== undefined &&
    kept.length > 0 &&
    kept.every((item) => item.state === "approved" || item.can_approve);
  const allApproved =
    kept !== undefined &&
    kept.length > 0 &&
    kept.every((item) => item.state === "approved");
  const latest = exports.data?.items[0];
  const exported =
    allApproved && latest !== undefined && latest.product_count === kept.length;
  useEffect(() => {
    onProgress?.({ reviewed, exported });
  }, [reviewed, exported, onProgress]);
  const openReviews = reviews.data
    ? reviews.data.items.filter((task) => task.status === "open").length
    : null;
  const withErrors =
    checks.data?.items.filter((item) => item.errors > 0).length ?? 0;
  const variants = useApiResource<VariantList>(`/batches/${batchId}/variants`);
  const sheets = useApiResource<SheetList>(`/batches/${batchId}/sheets`);
  const documents = useApiResource<DocumentList>(
    `/batches/${batchId}/documents`,
  );
  const rowNotes = run.errors.filter((error) => error.location !== null);
  const nothingToRead =
    sheets.data?.items.length === 0 && documents.data?.items.length === 0;

  if (nothingToRead) {
    return (
      <section className="section">
        <h2>Products</h2>
        <p className="muted">
          This batch has no spreadsheets, PDFs or photos with text, so no
          products were read from it.
        </p>
      </section>
    );
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: "products", label: "Products" },
    { id: "review", label: "Review" },
    { id: "copy", label: "Copy" },
    { id: "checks", label: "Checks" },
    { id: "export", label: "Export" },
    { id: "photos", label: "Photos" },
    { id: "sources", label: "Sources" },
  ];

  const counts: Partial<Record<Tab, number>> = {
    review: openReviews ?? 0,
    checks: withErrors,
    sources: rowNotes.length,
  };

  return (
    <>
      <NextStep
        batchId={batchId}
        openReviews={openReviews}
        enrichment={enrichment.data}
        validation={checks.data}
        approval={approval.data}
        onTab={setTab}
        onChanged={() => setVersion((current) => current + 1)}
      />
      <div className="tabs" role="tablist" aria-label="Batch results">
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            onClick={() => setTab(item.id)}
          >
            {item.label}
            {(counts[item.id] ?? 0) > 0 && (
              <span className="count">{counts[item.id]}</span>
            )}
          </button>
        ))}
      </div>

      {tab === "products" && (
        <>
          <ApprovalPanel
            batchId={batchId}
            approval={approval.data}
            onChanged={() => setVersion((current) => current + 1)}
          />
          <section className="section">
            <h2>Products</h2>
            <ProductSummaryText run={run} />
            {products.error && (
              <p className="error" role="alert">
                {products.error.message}
              </p>
            )}
            {products.data && products.data.items.length > 0 && (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th scope="col">#</th>
                      <th scope="col">Product</th>
                      <th scope="col">Name</th>
                      <th scope="col">Values</th>
                    </tr>
                  </thead>
                  <tbody>
                    {products.data.items.map((product) => (
                      <tr key={product.id}>
                        <td>{product.source_order}</td>
                        <td>
                          <Link href={`/products/${product.id}`}>
                            {product.product_key}
                          </Link>
                          {product.identifier_flags.map((flag) => (
                            <p key={flag} className="error file-reason">
                              {FLAG_TEXT[flag] ?? flag}
                            </p>
                          ))}
                        </td>
                        <td>
                          {product.name ?? <span className="muted">—</span>}
                        </td>
                        <td>{product.observation_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {(variants.data?.items.length ?? 0) > 0 && (
            <section className="section">
              <h2>Possible variants</h2>
              <p className="muted">
                These look like versions of one product. Confirm a pair and the
                Shopify export lists them as one product with options; the
                products themselves stay separate.
              </p>
              <ul className="list">
                {(variants.data?.items ?? []).map((pair) => (
                  <li key={pair.id}>
                    <span>
                      {pair.product_a_key} and {pair.product_b_key}
                    </span>
                    <span className="muted">
                      {pair.reasons.map((reason) => reason.text).join(" ")}
                    </span>
                    <VariantActions
                      pair={pair}
                      onDecided={() => {
                        variants.reload();
                        setVersion((current) => current + 1);
                      }}
                    />
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}

      {tab === "review" && (
        <>
          <p className="muted">
            Photos to confirm, values where sources disagree, and categories the
            AI wasn&apos;t sure of. When nothing is listed, nothing is waiting
            for you.
          </p>
          <ReviewPanel
            batchId={batchId}
            onDecided={() => {
              setVersion((current) => current + 1);
              products.reload();
            }}
          />
        </>
      )}
      {tab === "copy" && (
        <EnrichmentPanel batchId={batchId} version={version} />
      )}
      {tab === "checks" && <ChecksPanel batchId={batchId} version={version} />}
      {tab === "export" && (
        <ExportPanel
          batchId={batchId}
          approval={approval.data}
          version={version}
          onMade={() => setVersion((current) => current + 1)}
        />
      )}
      {tab === "photos" && <ImageGrid batchId={batchId} version={version} />}
      {tab === "sources" && (
        <>
          {(sheets.data?.items.length ?? 0) > 0 && (
            <section className="section">
              <h2>Sheets</h2>
              {(sheets.data?.items ?? []).map((sheet) => (
                <SheetCard key={sheet.id} sheet={sheet} />
              ))}
            </section>
          )}
          {sheets.error && (
            <p className="error" role="alert">
              {sheets.error.message}
            </p>
          )}

          {(documents.data?.items.length ?? 0) > 0 && (
            <section className="section">
              <h2>PDFs and photos</h2>
              <ul className="list">
                {(documents.data?.items ?? []).map((document) => (
                  <DocumentRow
                    key={document.source_file_id}
                    document={document}
                  />
                ))}
              </ul>
            </section>
          )}
          {documents.error && (
            <p className="error" role="alert">
              {documents.error.message}
            </p>
          )}

          {rowNotes.length > 0 && <RowNotes notes={rowNotes} />}
          {(sheets.data?.items.length ?? 0) === 0 &&
            (documents.data?.items.length ?? 0) === 0 && (
              <p className="muted">
                No spreadsheets, PDFs or photos were read.
              </p>
            )}
        </>
      )}
    </>
  );
}

const VARIANT_TEXT: Record<VariantPair["status"], string> = {
  flagged: "",
  confirmed: "Confirmed as variants",
  dismissed: "Not variants",
};

/** Confirm or dismiss a possible variant pair. Either way both products need approving again. */
function VariantActions({
  pair,
  onDecided,
}: {
  pair: VariantPair;
  onDecided: () => void;
}) {
  const [busy, setBusy] = useState<VariantPair["status"] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function decide(decision: "confirmed" | "dismissed") {
    setBusy(decision);
    setError(null);
    try {
      await api<VariantDecided>(
        `/variants/${pair.id}/decision`,
        jsonBody("POST", { decision }),
      );
      onDecided();
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Couldn't save that.",
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <span className="variant-actions">
      {pair.status !== "flagged" && (
        <span className="badge">{VARIANT_TEXT[pair.status]}</span>
      )}
      {pair.status !== "confirmed" && (
        <button
          type="button"
          className="button small"
          disabled={busy !== null}
          aria-busy={busy === "confirmed" || undefined}
          onClick={() => void decide("confirmed")}
        >
          These are variants
        </button>
      )}
      {pair.status !== "dismissed" && (
        <button
          type="button"
          className="button small"
          disabled={busy !== null}
          aria-busy={busy === "dismissed" || undefined}
          onClick={() => void decide("dismissed")}
        >
          Not variants
        </button>
      )}
      {error && (
        <span className="error" role="alert">
          {error}
        </span>
      )}
    </span>
  );
}

function ProductSummaryText({ run }: { run: BatchRun }) {
  if (run.products_found === null) return null;
  if (run.products_found === 0) {
    return <p className="muted">No products were found in the spreadsheets.</p>;
  }
  return (
    <p>
      Processed {run.products_kept} of {run.products_found} products.
      {run.products_discarded
        ? ` ${run.products_discarded} came after your limit of ${run.products_kept} and weren't processed. Products are kept in file order.`
        : ""}
    </p>
  );
}

function sheetTitle(sheet: Sheet): string {
  return sheet.sheet_name
    ? `${sheet.source_file_name} · ${sheet.sheet_name}`
    : sheet.source_file_name;
}

function SheetCard({ sheet }: { sheet: Sheet }) {
  const named = sheet.columns.filter((column) => column.header);
  const mapped = named.filter((column) => column.attribute);
  const unmapped = named.filter((column) => !column.attribute);
  const counts = [
    `${sheet.row_count} rows read`,
    sheet.duplicate_rows
      ? `${sheet.duplicate_rows} repeated a product already seen`
      : "",
    sheet.rows_without_identifier
      ? `${sheet.rows_without_identifier} had no SKU, model number or barcode and were skipped`
      : "",
    sheet.rows_over_cap
      ? `${sheet.rows_over_cap} were past the row limit and weren't read`
      : "",
    sheet.unreadable_rows ? `${sheet.unreadable_rows} couldn't be parsed` : "",
  ].filter(Boolean);

  return (
    <div className="notice stack">
      <div className="title-row">
        <h3>{sheetTitle(sheet)}</h3>
        {sheet.hidden && <span className="badge">hidden sheet</span>}
        <span className="badge">
          {sheet.status === "used" ? "used" : "not used"}
        </span>
      </div>
      {sheet.skip_message && <p className="error">{sheet.skip_message}</p>}
      {sheet.mapping_message && (
        <p className="error">{sheet.mapping_message}</p>
      )}
      {sheet.status === "used" && (
        <p className="muted">
          Column names are in row {sheet.header_row}
          {sheet.header_rows === 2 ? " and the row above it" : ""}.{" "}
          {MAPPING_TEXT[sheet.mapping_source]} {counts.join("; ")}.
        </p>
      )}
      {mapped.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Column</th>
                <th scope="col">Name in file</th>
                <th scope="col">Read as</th>
                <th scope="col">How</th>
              </tr>
            </thead>
            <tbody>
              {mapped.map((column) => (
                <tr key={column.letter}>
                  <td>{column.letter}</td>
                  <td>{column.header}</td>
                  <td>{column.attribute}</td>
                  <td>
                    {column.source === "llm"
                      ? `LLM, ${Math.round((column.confidence ?? 0) * 100)}% sure${column.reason ? `: ${column.reason}` : ""}`
                      : "Known name"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {unmapped.length > 0 && (
        <p className="muted">
          Not mapped, but kept with each row:{" "}
          {unmapped
            .map(
              (column) =>
                `${column.header} (${column.letter}${column.suggestion ? `, possibly ${column.suggestion}` : ""})`,
            )
            .join(", ")}
          .
        </p>
      )}
    </div>
  );
}

function DocumentRow({ document }: { document: DocumentFile }) {
  const read =
    document.kind === "pdf"
      ? `${document.pages} pages${document.pages_ocr ? `, ${document.pages_ocr} read by OCR` : ""}`
      : "photo, read by OCR";
  const found = [
    document.values_kept ? `${document.values_kept} values kept` : "",
    document.values_dropped
      ? `${document.values_dropped} not found on the page and dropped`
      : "",
    document.pages_without_product
      ? `${document.pages_without_product} ${document.kind === "pdf" ? "pages" : "photo"} named no product`
      : "",
  ].filter(Boolean);
  return (
    <li>
      <span>
        {document.parent_file_id ? "↳ " : ""}
        {document.source_file_name}{" "}
        <span className="badge">
          {document.status === "read" ? "read" : "not read"}
        </span>
      </span>
      <span className="muted">
        {read}
        {document.product_keys.length > 0 &&
          ` · products: ${document.product_keys.join(", ")}`}
        {found.length > 0 && ` · ${found.join("; ")}`}
      </span>
      {document.skip_message && (
        <p className="error file-reason">{document.skip_message}</p>
      )}
      {document.llm_message && (
        <p className="error file-reason">{document.llm_message}</p>
      )}
    </li>
  );
}

function RowNotes({ notes }: { notes: RunError[] }) {
  return (
    <section className="section">
      <h2>Rows, pages and values that weren&apos;t used</h2>
      <ul className="list">
        {notes.map((note, index) => (
          <li key={`${note.created_at}-${index}`}>
            <span>{describeLocation(note.location) || "This sheet"}</span>
            <span className="muted">{note.message}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
