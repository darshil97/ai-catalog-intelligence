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
  products_found: number | null;
  products_kept: number | null;
  products_discarded: number | null;
  image_duplicate_groups: number | null;
  images_matched: number | null;
  images_to_review: number | null;
  images_unmatched: number | null;
  stages: RunStage[];
  errors: RunError[];
};

// A failure on our side, or a row or cell that couldn't be used.
export type RunError = {
  error_code: string;
  message: string;
  source_file_id: string | null;
  location: CellLocation | null;
  created_at: string;
};

// Where a value was read from. Spreadsheets give sheet (null for a CSV), row and column; PDFs
// and photos give page (null for a photo), the quote it was read from, and its box.
export type CellLocation = {
  sheet?: string | null;
  sheet_index?: number;
  row?: number;
  column?: string;
  header?: string;
  merged_from?: string;
  page?: number | null;
  method?: "text" | "ocr";
  quote?: string;
  bbox?: [number, number, number, number] | null;
  page_width?: number;
  page_height?: number;
};

export type Product = {
  id: string;
  batch_id: string;
  product_key: string;
  key_source: "sku" | "model_number" | "gtin";
  supplier_sku: string | null;
  identifier_flags: string[];
  source_order: number;
  status: string;
  created_at: string;
};

export type ProductSummary = Product & {
  name: string | null;
  observation_count: number;
};

export type ProductList = { batch_id: string; items: ProductSummary[] };

export type Observation = {
  id: string;
  attribute_code: string;
  attribute_name: string;
  raw_value: string;
  source_file_id: string;
  source_file_name: string;
  source_record_id: string | null;
  location: CellLocation;
  extractor: string;
  confidence: number | null;
  mapped_by: "known_name" | "llm";
};

export type ProductImage = {
  image_id: string;
  image_name: string;
  position: number;
  thumbnail_url: string | null;
  decision: "auto_accepted" | "accepted";
  decided_by: string;
};

export type StandardValue = {
  id: string;
  display: string;
  unit: string | null;
  flags: string[];
  raw_value: string;
  source_file_name: string;
  location: CellLocation;
  rule_version: string;
};

export type CatalogValue = {
  code: string;
  name: string;
  state: "verified" | "conflict" | "unclear" | "missing" | "reviewer_set";
  shown: string | null;
  decided_by: string | null;
  values: StandardValue[];
};

export type CategoryRef = { id: string; code: string; name: string };

export type ProductCategory = {
  state: "none" | "assigned" | "needs_review";
  source: "llm" | "person" | null;
  confidence: number | null;
  reason: string | null;
  category: CategoryRef | null;
  suggested: CategoryRef | null;
};

export type CategoryNode = CategoryRef & { path: string };

export type ProductDetail = Product & {
  observations: Observation[];
  catalog_values: CatalogValue[];
  category: ProductCategory | null;
  images: ProductImage[];
  differing_attributes: string[];
  generated_content: GeneratedContent | null;
  validation: ProductValidation | null;
};

export type SheetColumn = {
  index: number;
  letter: string;
  header: string;
  attribute: string | null;
  source: "known_name" | "llm" | null;
  confidence: number | null;
  reason: string | null;
  suggestion: string | null;
};

export type Sheet = {
  id: string;
  source_file_id: string;
  source_file_name: string;
  sheet_index: number;
  sheet_name: string | null;
  hidden: boolean;
  status: "used" | "skipped";
  skip_reason: string | null;
  skip_message: string | null;
  header_row: number | null;
  header_rows: number | null;
  columns: SheetColumn[];
  mapping_source:
    | "none"
    | "known_names"
    | "saved"
    | "llm"
    | "llm_failed"
    | "llm_capped"
    | "llm_budget";
  mapping_message: string | null;
  row_count: number;
  rows_without_identifier: number;
  duplicate_rows: number;
  rows_over_cap: number;
  unreadable_rows: number;
};

export type SheetList = { batch_id: string; items: Sheet[] };

export type DocumentFile = {
  source_file_id: string;
  source_file_name: string;
  parent_file_id: string | null;
  kind: "pdf" | "image";
  status: "read" | "skipped";
  skip_reason: string | null;
  skip_message: string | null;
  pages: number;
  pages_with_text: number;
  pages_ocr: number;
  pages_unreadable: number;
  characters: number;
  product_keys: string[];
  pages_without_product: number;
  values_kept: number;
  values_dropped: number;
  llm_source: "none" | "llm" | "llm_failed" | "llm_capped" | "llm_budget";
  llm_message: string | null;
};

export type DocumentList = { batch_id: string; items: DocumentFile[] };

export type BatchImage = {
  id: string;
  source_file_id: string;
  source_file_name: string;
  parent_file_id: string | null;
  parent_file_name: string | null;
  status: "processed" | "skipped";
  skip_reason: string | null;
  skip_message: string | null;
  width: number | null;
  height: number | null;
  thumbnail_url: string | null;
  embedded: boolean;
  embedding_note: string | null;
  duplicate_group_id: string | null;
  duplicate_kind: "exact" | "near" | null;
  group_size: number;
  is_representative: boolean;
};

export type BatchImageList = { batch_id: string; items: BatchImage[] };

export type MatchEvidence = {
  signal: string;
  points: number;
  text: string;
  detail: Record<string, unknown>;
};

export type MatchCandidate = {
  id: string;
  product_id: string;
  product_key: string;
  score: number;
  decision: "auto_accepted" | "needs_review" | "accepted" | "rejected";
  decided_by: string;
  evidence: MatchEvidence[];
};

export type ImageMatch = MatchCandidate & {
  image_id: string;
  source_file_id: string;
  image_name: string;
  decided_at: string;
  values_read_at: string | null;
};

export type ImageMatchList = { batch_id: string; items: ImageMatch[] };

export type ConflictValue = {
  id: string;
  display: string;
  raw_value: string;
  source_file_name: string;
  location: CellLocation;
  flags: string[];
};

export type ReviewTask = {
  id: string;
  kind: "image_match" | "attribute_conflict" | "category";
  status: "open" | "resolved";
  created_at: string;
  resolved_at: string | null;
  image_id: string | null;
  source_file_id: string | null;
  image_name: string | null;
  thumbnail_url: string | null;
  candidates: MatchCandidate[];
  product_id: string | null;
  product_key: string | null;
  attribute_code: string | null;
  attribute_name: string | null;
  values: ConflictValue[];
  suggested_category_id: string | null;
  suggested_category_name: string | null;
  category_reason: string | null;
};

export type ReviewTaskList = {
  batch_id: string;
  pending_photo_values: number;
  items: ReviewTask[];
};

export type PhotoValuesStatus = "queued" | "waiting" | "not_needed";

export type DecisionResult = {
  task_id: string;
  accepted_product_id: string | null;
  shown: string | null;
  photo_values: PhotoValuesStatus;
};

export type VariantPair = {
  id: string;
  status: "flagged" | "confirmed" | "dismissed";
  product_a_id: string;
  product_a_key: string;
  product_b_id: string;
  product_b_key: string;
  reasons: { rule: string; text: string }[];
};

export type VariantList = { batch_id: string; items: VariantPair[] };

// Generated titles, descriptions and bullets (M7).

export type EnrichmentState =
  "none" | "generated" | "out_of_date" | "skipped" | "failed";

export type EnrichmentRun = {
  id: string;
  status:
    "queued" | "running" | "paused" | "succeeded" | "failed" | "cancelled";
  pause_reason: string | null;
  blocked: number;
  waiting: number;
  done: number;
  total: number;
  created_at: string;
  finished_at: string | null;
};

export type ProductEnrichment = {
  product_id: string;
  product_key: string;
  state: EnrichmentState;
  message: string | null;
  version: number | null;
  title: string | null;
  facts: number;
  left_out: number;
  can_generate: boolean;
};

export type BatchEnrichment = {
  batch_id: string;
  open_reviews: number;
  can_generate: number;
  budget_reached: boolean;
  run: EnrichmentRun | null;
  items: ProductEnrichment[];
};

export type EnrichmentStarted = { run_id: string; products: number };

export type Citation = {
  code: string;
  name: string;
  display: string;
  state: string;
  attribute_id: string | null;
};

export type GeneratedClaim = {
  field: "title" | "description" | "bullets";
  index: number;
  text: string;
  facts: Citation[];
};

export type LeftOutAttribute = {
  code: string;
  name: string;
  state: string;
  reason: string;
};

export type GeneratedContent = {
  version: number | null;
  state: EnrichmentState;
  message: string | null;
  title: string | null;
  description: string | null;
  bullets: string[];
  claims: GeneratedClaim[];
  status: string | null;
  model_id: string | null;
  prompt_version: string | null;
  rules_version: string | null;
  created_at: string | null;
  left_out: LeftOutAttribute[];
  can_generate: boolean;
};

// Catalog checks (M8).

export type Severity = "error" | "warning" | "info";

export type ValidationIssue = {
  rule: string;
  rule_version: number;
  severity: Severity;
  subject: string;
  message: string;
  other_product_id: string | null;
  fix: "review" | "generate" | "photos" | null;
  set_aside: { reason: string; by: string; at: string } | null;
};

export type ProductValidation = {
  profile: string;
  profile_name: string;
  can_approve: boolean;
  errors: number;
  warnings: number;
  info: number;
  issues: ValidationIssue[];
};

export type BatchValidation = {
  batch_id: string;
  profile: string;
  profile_name: string;
  profiles: { code: string; name: string }[];
  rules_version: string;
  ready: number;
  items: (ProductValidation & { product_id: string; product_key: string })[];
};
