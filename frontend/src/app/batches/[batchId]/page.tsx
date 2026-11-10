"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState, type DragEvent } from "react";

import { RequireActive } from "@/components/Account";
import { ConfirmButton } from "@/components/ConfirmButton";
import { Extraction } from "@/components/Extraction";
import { ApiError, api } from "@/lib/api";
import type {
  Batch,
  BatchRun,
  Me,
  SourceFile,
  SourceFileList,
} from "@/lib/types";
import { formatBytes, uploadFile, type UploadProgress } from "@/lib/upload";
import { useApiResource } from "@/lib/useApiResource";

const ACCEPT = ".csv,.xlsx,.pdf,.jpg,.jpeg,.png,.webp,.zip";
const EDITABLE = new Set(["draft", "failed"]);
const ACTIVE_RUN = new Set(["queued", "running"]);
const POLL_MS = 5000;

type LocalUpload = { key: string; name: string; progress: UploadProgress };

export default function BatchPage() {
  return (
    <main className="page">
      <RequireActive>
        <BatchDetail />
      </RequireActive>
    </main>
  );
}

function BatchDetail() {
  const { batchId } = useParams<{ batchId: string }>();
  const batch = useApiResource<Batch>(`/batches/${batchId}`);
  const files = useApiResource<SourceFileList>(`/batches/${batchId}/files`);
  const run = useApiResource<BatchRun | null>(`/batches/${batchId}/run`);
  const me = useApiResource<Me>("/me");
  const [uploads, setUploads] = useState<LocalUpload[]>([]);
  const [actionError, setActionError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const status = batch.data?.status;
  const running = run.data ? ACTIVE_RUN.has(run.data.status) : false;
  const { reload: reloadBatch } = batch;
  const { reload: reloadFiles } = files;
  const { reload: reloadRun } = run;

  // While processing, check progress every few seconds.
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      reloadRun();
      reloadBatch();
      reloadFiles();
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, reloadRun, reloadBatch, reloadFiles]);

  const loadError =
    batch.error?.status === 404
      ? "This batch doesn't exist, or isn't yours."
      : batch.error?.message;
  if (!batch.data) {
    return loadError ? (
      <p className="error" role="alert">
        {loadError}
      </p>
    ) : (
      <p className="muted">Loading…</p>
    );
  }

  const editable = EDITABLE.has(batch.data.status);
  const uploading = uploads.some(
    (upload) => !["done", "failed"].includes(upload.progress.phase),
  );
  const uploadedCount =
    files.data?.items.filter(
      (file) => !file.parent_file_id && file.status !== "rejected",
    ).length ?? 0;

  async function addFiles(chosen: FileList | File[]) {
    setActionError(null);
    for (const file of Array.from(chosen)) {
      const key = `${file.name}-${file.size}-${Date.now()}-${Math.random()}`;
      const update = (progress: UploadProgress) =>
        setUploads((current) =>
          current.some((item) => item.key === key)
            ? current.map((item) =>
                item.key === key ? { ...item, progress } : item,
              )
            : [...current, { key, name: file.name, progress }],
        );
      try {
        const result = await uploadFile(batchId, file, update);
        update({
          phase: result.status === "rejected" ? "failed" : "done",
          fraction: 1,
          message: result.rejection_message ?? undefined,
        });
      } catch (caught) {
        update({
          phase: "failed",
          fraction: 0,
          message:
            caught instanceof ApiError
              ? caught.message
              : "The upload failed. Try again.",
        });
      }
      reloadFiles();
    }
  }

  async function removeFile(file: SourceFile) {
    setActionError(null);
    try {
      await api<void>(`/files/${file.id}`, { method: "DELETE" });
      reloadFiles();
    } catch (caught) {
      setActionError(
        caught instanceof ApiError
          ? caught.message
          : "Couldn't remove this file.",
      );
    }
  }

  async function process() {
    setSubmitting(true);
    setActionError(null);
    try {
      await api<BatchRun>(`/batches/${batchId}/process`, { method: "POST" });
      setUploads([]);
    } catch (caught) {
      setActionError(
        caught instanceof ApiError
          ? caught.message
          : "Couldn't start processing.",
      );
    } finally {
      setSubmitting(false);
      reloadBatch();
      reloadRun();
      reloadFiles();
      me.reload();
    }
  }

  return (
    <div className="stack">
      <p>
        <Link href={`/suppliers/${batch.data.supplier_id}`}>
          ← {batch.data.supplier_name ?? "Supplier"}
        </Link>
      </p>
      <div className="title-row">
        <h1>{batch.data.name}</h1>
        <span className="badge">{status}</span>
      </div>
      {actionError && (
        <p className="error" role="alert">
          {actionError}
        </p>
      )}

      {editable && (
        <DropZone disabled={uploading || submitting} onFiles={addFiles} />
      )}
      {uploads.length > 0 && <UploadList uploads={uploads} />}

      <h2>Files</h2>
      <FileList
        files={files.data?.items ?? []}
        editable={editable && !uploading}
        onRemove={removeFile}
      />

      {editable && (
        <div className="stack">
          <ConfirmButton
            className="button primary"
            disabled={uploading || submitting || uploadedCount === 0}
            label={
              submitting
                ? "Starting…"
                : batch.data.status === "failed"
                  ? "Try processing again"
                  : "Process batch"
            }
            question={processQuestion(me.data)}
            confirmLabel="Process"
            onConfirm={process}
          />
          <p className="muted">{usageText(me.data)}</p>
        </div>
      )}

      {run.data && <RunPanel run={run.data} />}
      {run.data?.status === "succeeded" && (
        // Keyed by the run, so a batch processed again loads its new results.
        <Extraction key={run.data.run_id} batchId={batchId} run={run.data} />
      )}
    </div>
  );
}

function submissionsLeft(me: Me | null): number | null {
  if (!me?.limits || !me.usage) return null;
  const left = [
    [me.limits.submissions_lifetime, me.usage.submissions_lifetime],
    [me.limits.submissions_per_24_hours, me.usage.submissions_per_24_hours],
  ]
    .filter(([limit]) => limit !== null && limit !== undefined)
    .map(([limit, used]) => Math.max(0, (limit as number) - (used ?? 0)));
  return left.length ? Math.min(...left) : null;
}

function processQuestion(me: Me | null): string {
  const left = submissionsLeft(me);
  const note = left === null ? "" : ` You have ${left} left.`;
  return `This uses one submission.${note}`;
}

function usageText(me: Me | null): string {
  const left = submissionsLeft(me);
  const limits = me?.limits;
  const rules = limits
    ? `Up to ${limits.files_per_submission ?? "any number of"} files, ${formatBytes(limits.max_file_bytes ?? 0)} each, ${formatBytes(limits.max_submission_bytes ?? 0)} in total.`
    : "";
  if (left === null) return rules;
  return `${rules} Processing uses one submission; you have ${left} left.`;
}

function DropZone({
  disabled,
  onFiles,
}: {
  disabled: boolean;
  onFiles: (files: File[]) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  function drop(event: DragEvent) {
    event.preventDefault();
    setOver(false);
    if (!disabled) onFiles(Array.from(event.dataTransfer.files));
  }

  return (
    <div
      className={`drop-zone${over ? " over" : ""}`}
      onDragOver={(event) => {
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
    >
      <p>Drop files here, or</p>
      <button
        type="button"
        className="button"
        disabled={disabled}
        onClick={() => input.current?.click()}
      >
        Choose files
      </button>
      <input
        ref={input}
        type="file"
        multiple
        accept={ACCEPT}
        hidden
        onChange={(event) => {
          if (event.target.files) onFiles(Array.from(event.target.files));
          event.target.value = "";
        }}
      />
      <p className="muted">CSV, XLSX, PDF, JPEG, PNG, WebP or ZIP.</p>
    </div>
  );
}

const PHASE_TEXT: Record<UploadProgress["phase"], string> = {
  hashing: "Preparing…",
  registering: "Checking limits…",
  uploading: "Uploading",
  checking: "Confirming…",
  done: "Uploaded",
  failed: "Not uploaded",
};

function UploadList({ uploads }: { uploads: LocalUpload[] }) {
  return (
    <ul className="list" aria-live="polite">
      {uploads.map((upload) => (
        <li key={upload.key}>
          <span>{upload.name}</span>
          <span
            className={upload.progress.phase === "failed" ? "error" : "muted"}
          >
            {PHASE_TEXT[upload.progress.phase]}
            {upload.progress.phase === "uploading" &&
              ` ${Math.round(upload.progress.fraction * 100)}%`}
            {upload.progress.message && `: ${upload.progress.message}`}
          </span>
        </li>
      ))}
    </ul>
  );
}

const STATUS_TEXT: Record<SourceFile["status"], string> = {
  pending_upload: "uploading",
  uploaded: "ready",
  ingested: "kept",
  rejected: "not used",
};

function FileList({
  files,
  editable,
  onRemove,
}: {
  files: SourceFile[];
  editable: boolean;
  onRemove: (file: SourceFile) => void;
}) {
  if (files.length === 0) return <p className="muted">No files yet.</p>;
  return (
    <ul className="list">
      {files.map((file) => (
        <li key={file.id} className={file.parent_file_id ? "child" : undefined}>
          <span>
            {file.parent_file_id ? "↳ " : ""}
            {file.original_name}{" "}
            <span className="badge">{STATUS_TEXT[file.status]}</span>
          </span>
          <span className="muted">
            {formatBytes(file.size_bytes)}
            {typeof file.details.pages === "number" &&
              ` · ${file.details.pages} pages`}
            {typeof file.details.entries === "number" &&
              ` · ${file.details.kept as number} of ${file.details.entries} entries kept`}
          </span>
          {editable && !file.parent_file_id && (
            <span className="actions">
              <button
                type="button"
                className="button link danger"
                onClick={() => onRemove(file)}
              >
                Remove
              </button>
            </span>
          )}
          {file.rejection_message && (
            <p className="error file-reason">{file.rejection_message}</p>
          )}
        </li>
      ))}
    </ul>
  );
}

function RunPanel({ run }: { run: BatchRun }) {
  const done = run.finished_at !== null;
  return (
    <section className="notice" aria-live="polite">
      <h2>Processing</h2>
      {!done && (
        <p>
          {run.status === "queued"
            ? "Waiting for the worker to start. This can take a couple of minutes."
            : "Checking files and reading spreadsheets, PDFs and photos…"}{" "}
          {run.stages
            .map(
              (stage) =>
                `${stage.stage}: ${stage.completed_chunks}/${stage.total_chunks}`,
            )
            .join(" · ")}
        </p>
      )}
      {run.status === "succeeded" && (
        <p>
          Kept {run.files_kept} of {run.files_found} files
          {run.images_found
            ? `, including ${run.images_kept} of ${run.images_found} images`
            : ""}
          .
          {run.files_discarded
            ? ` ${run.files_discarded} weren't used; each one says why above.`
            : ""}
        </p>
      )}
      {run.status === "failed" && (
        <p className="error">
          Processing failed on our side, so your submission was given back. You
          can try again.
        </p>
      )}
    </section>
  );
}
