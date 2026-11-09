"use client";

import { ApiError, api, jsonBody } from "@/lib/api";
import type { SourceFile, UploadRegistration } from "@/lib/types";

export type UploadPhase =
  "hashing" | "registering" | "uploading" | "checking" | "done" | "failed";

export type UploadProgress = {
  phase: UploadPhase;
  /** 0 to 1, while uploading. */
  fraction: number;
  message?: string;
};

/** Hex SHA-256 of a file, computed in the browser so storage can verify the upload. */
async function sha256(file: File): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    await file.arrayBuffer(),
  );
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

/** POSTs the form to storage, reporting progress. Resolves once storage accepts it. */
function postForm(
  target: UploadRegistration["upload"],
  file: File,
  onProgress: (fraction: number) => void,
): Promise<void> {
  const form = new FormData();
  for (const [name, value] of Object.entries(target.fields))
    form.append(name, value);
  // Storage requires the file to be the last field.
  form.append("file", file);
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", target.url);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    request.onload = () =>
      request.status >= 200 && request.status < 300
        ? resolve()
        : reject(
            new ApiError(
              request.status,
              "upload_failed",
              "Storage refused the upload. Try again.",
            ),
          );
    request.onerror = () =>
      reject(
        new ApiError(
          0,
          "network",
          "The upload was interrupted. Check your connection and try again.",
        ),
      );
    request.send(form);
  });
}

/**
 * Uploads one file to a batch: hash, register, send straight to storage, then confirm.
 *
 * @returns The file as the API recorded it after the upload.
 * @throws ApiError with the API's code and message when a step is refused.
 */
export async function uploadFile(
  batchId: string,
  file: File,
  onProgress: (progress: UploadProgress) => void,
): Promise<SourceFile> {
  onProgress({ phase: "hashing", fraction: 0 });
  const hash = await sha256(file);
  onProgress({ phase: "registering", fraction: 0 });
  const registration = await api<UploadRegistration>(
    `/batches/${batchId}/files`,
    jsonBody("POST", { name: file.name, size_bytes: file.size, sha256: hash }),
  );
  onProgress({ phase: "uploading", fraction: 0 });
  await postForm(registration.upload, file, (fraction) =>
    onProgress({ phase: "uploading", fraction }),
  );
  onProgress({ phase: "checking", fraction: 1 });
  return api<SourceFile>(`/files/${registration.file.id}/complete`, {
    method: "POST",
  });
}

/** Shows a size the way people read it, such as "2.4 MB". */
export function formatBytes(size: number): string {
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  if (size >= 1024) return `${Math.round(size / 1024)} KB`;
  return `${size} bytes`;
}
