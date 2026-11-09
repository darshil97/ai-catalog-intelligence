// API response shapes, mirroring backend/app/api/schemas. Kept by hand until openapi-typescript
// supports TypeScript 6; the backend's /openapi.json is the reference.

export type Me = {
  id: string;
  email: string;
  role: "user" | "admin";
  status: "active" | "waitlisted";
  limits?: Record<string, number | null>;
  usage?: Record<string, number>;
};

export type Supplier = {
  id: string;
  name: string;
  batch_count: number;
  created_at: string;
  updated_at: string;
};

export type SupplierList = { items: Supplier[] };

export type Batch = {
  id: string;
  supplier_id: string;
  supplier_name?: string | null;
  name: string;
  status: string;
  expires_at: string;
  created_at: string;
  updated_at: string;
};

export type BatchList = {
  supplier_id: string;
  supplier_name: string;
  items: Batch[];
};

export type SourceFile = {
  id: string;
  batch_id: string;
  parent_file_id: string | null;
  archive_member: string | null;
  position: number;
  member_index: number;
  original_name: string;
  extension: string;
  kind: "spreadsheet" | "pdf" | "image" | "zip" | "other";
  size_bytes: number;
  sha256: string | null;
  status: "pending_upload" | "uploaded" | "ingested" | "rejected";
  rejection_reason: string | null;
  rejection_message: string | null;
  details: Record<string, unknown>;
  upload_expires_at: string | null;
  created_at: string;
  updated_at: string;
};

export type SourceFileList = { batch_id: string; items: SourceFile[] };

export type UploadRegistration = {
  file: SourceFile;
  upload: { url: string; fields: Record<string, string>; expires_at: string };
  resumed: boolean;
};

export type RunStage = {
  stage: string;
  position: number;
  status: string;
  total_chunks: number;
  completed_chunks: number;
  failed_chunks: number;
};

export type BatchRun = {
  run_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  files_found: number | null;
  files_kept: number | null;
  files_discarded: number | null;
  images_found: number | null;
  images_kept: number | null;
  images_discarded: number | null;
  stages: RunStage[];
  errors: {
    error_code: string;
    message: string;
    source_file_id: string | null;
    created_at: string;
  }[];
};
