"use client";

import { useEffect, useState } from "react";

import { Working } from "@/components/Working";
import { ApiError, api, jsonBody } from "@/lib/api";
import type {
  BatchApproval,
  DownloadLink,
  ExportFormat,
  ExportList,
  ExportMade,
} from "@/lib/types";
import { useApiResource } from "@/lib/useApiResource";

const FORMATS: { id: ExportFormat; name: string; text: string }[] = [
  {
    id: "csv",
    name: "CSV",
    text: "One row per product with every attribute, the copy and photo links. Opens in any spreadsheet.",
  },
  {
    id: "xlsx",
    name: "Excel",
    text: "The same rows, plus a Sources sheet showing where every value came from.",
  },
  {
    id: "shopify_csv",
    name: "Shopify",
    text: "Shopify's product import file. Products arrive as drafts, with confirmed variants grouped.",
  },
];

const FORMAT_NAME: Record<string, string> = {
  csv: "CSV",
  xlsx: "Excel",
  shopify_csv: "Shopify",
};

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function size(bytes: number | null): string {
  if (bytes === null) return "";
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Files of a batch's approved products: make one, download it, and see past ones (D41). */
export function ExportPanel({
  batchId,
  approval,
  version,
}: {
  batchId: string;
  approval: BatchApproval | null;
  version: number;
}) {
  const exports = useApiResource<ExportList>(`/batches/${batchId}/exports`);
  const { reload } = exports;
  const [making, setMaking] = useState<ExportFormat | null>(null);
  const [made, setMade] = useState<ExportMade | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    reload();
  }, [version, reload]);

  const approved = approval?.approved ?? 0;
  const total = approval?.items.length ?? 0;

  async function make(format: ExportFormat) {
    setMaking(format);
    setError(null);
    setMade(null);
    try {
      const result = await api<ExportMade>(
        `/batches/${batchId}/exports`,
        jsonBody("POST", { format }),
      );
      setMade(result);
      window.location.assign(result.download_url);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Couldn't make the file.",
      );
    } finally {
      setMaking(null);
      reload();
    }
  }

  async function download(id: string) {
    setOpening(id);
    setError(null);
    try {
      const link = await api<DownloadLink>(`/exports/${id}/download`);
      window.location.assign(link.url);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Couldn't open the file.",
      );
    } finally {
      setOpening(null);
    }
  }

  return (
    <>
      <section className="section">
        <h2>Export</h2>
        <p className="muted">
          {approved === 0
            ? "Only approved products are exported, and none is approved yet. Approve products on the Products tab first."
            : `${approved} of ${total} ${total === 1 ? "product is" : "products are"} approved and will be included. Products changed since approval are left out until approved again.`}
        </p>
        <div className="export-formats">
          {FORMATS.map((format) => (
            <div className="card export-format" key={format.id}>
              <h3>{format.name}</h3>
              <p className="muted">{format.text}</p>
              <button
                type="button"
                className="button primary"
                disabled={approved === 0 || making !== null}
                aria-busy={making === format.id || undefined}
                onClick={() => void make(format.id)}
              >
                {making === format.id ? "Making…" : `Download ${format.name}`}
              </button>
            </div>
          ))}
        </div>
        {making && (
          <Working
            label={`Making the ${FORMAT_NAME[making]} file and signing photo links…`}
            compact
          />
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {made && (
          <div className="notice" role="status">
            <p>
              {FORMAT_NAME[made.format]} file made with {made.product_count}{" "}
              {made.product_count === 1 ? "product" : "products"}. The download
              should start; if it doesn&apos;t,{" "}
              <a href={made.download_url}>open it here</a>.
            </p>
            {made.links_expire_at && (
              <p className="muted">
                Photo links in the file stop working on{" "}
                {when(made.links_expire_at)}. Import it before then, or make a
                new file.
              </p>
            )}
            {made.notes.length > 0 && (
              <ul className="reasons">
                {made.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            )}
            {made.left_out.length > 0 && (
              <>
                <p className="muted">Left out:</p>
                <ul className="reasons">
                  {made.left_out.map((row) => (
                    <li key={row.product_key}>
                      {row.product_key}: {row.reason}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </section>

      {(exports.data?.items.length ?? 0) > 0 && (
        <section className="section">
          <h2>Past exports</h2>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Made</th>
                  <th>Format</th>
                  <th>Products</th>
                  <th>Photo links until</th>
                  <th>Size</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(exports.data?.items ?? []).map((row) => {
                  const expired =
                    row.links_expire_at !== null &&
                    new Date(row.links_expire_at) < new Date();
                  return (
                    <tr key={row.id}>
                      <td>{when(row.created_at)}</td>
                      <td>{FORMAT_NAME[row.format] ?? row.format}</td>
                      <td>
                        {row.product_count}
                        {row.left_out_count > 0 && (
                          <span className="muted">
                            {" "}
                            ({row.left_out_count} left out)
                          </span>
                        )}
                      </td>
                      <td className={expired ? "muted" : undefined}>
                        {row.links_expire_at
                          ? expired
                            ? "Expired"
                            : when(row.links_expire_at)
                          : "No photos"}
                      </td>
                      <td>{size(row.size_bytes)}</td>
                      <td>
                        <button
                          type="button"
                          className="button"
                          disabled={opening !== null}
                          aria-busy={opening === row.id || undefined}
                          onClick={() => void download(row.id)}
                        >
                          Download
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
