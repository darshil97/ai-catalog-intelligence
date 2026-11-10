"use client";

import Link from "next/link";

import { describeLocation, FLAG_TEXT } from "@/lib/location";
import type {
  BatchRun,
  ProductList,
  RunError,
  Sheet,
  SheetList,
} from "@/lib/types";
import { useApiResource } from "@/lib/useApiResource";

const MAPPING_TEXT: Record<Sheet["mapping_source"], string> = {
  none: "",
  known_names: "Columns recognised by name.",
  saved: "Columns mapped as last time for this supplier.",
  llm: "Columns the names didn't settle were mapped by the LLM.",
  llm_failed: "",
  llm_capped: "",
};

/**
 * What a finished run found in the batch's spreadsheets: products, how each sheet was read, and
 * rows that couldn't be used. Mounted once the run has finished, so it loads the final result.
 */
export function Extraction({
  batchId,
  run,
}: {
  batchId: string;
  run: BatchRun;
}) {
  const products = useApiResource<ProductList>(`/batches/${batchId}/products`);
  const sheets = useApiResource<SheetList>(`/batches/${batchId}/sheets`);
  const rowNotes = run.errors.filter((error) => error.location !== null);

  if (sheets.data && sheets.data.items.length === 0) {
    return (
      <section className="stack">
        <h2>Products</h2>
        <p className="muted">
          This batch has no spreadsheets, so no products were read from it.
        </p>
      </section>
    );
  }

  return (
    <>
      <section className="stack">
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
                    <td>{product.name ?? <span className="muted">—</span>}</td>
                    <td>{product.observation_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="stack">
        <h2>Sheets</h2>
        {sheets.error && (
          <p className="error" role="alert">
            {sheets.error.message}
          </p>
        )}
        {(sheets.data?.items ?? []).map((sheet) => (
          <SheetCard key={sheet.id} sheet={sheet} />
        ))}
      </section>

      {rowNotes.length > 0 && <RowNotes notes={rowNotes} />}
    </>
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
    sheet.unreadable_rows
      ? `${sheet.unreadable_rows} couldn't be parsed`
      : "",
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

function RowNotes({ notes }: { notes: RunError[] }) {
  return (
    <section className="stack">
      <h2>Rows and cells that weren&apos;t used</h2>
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
