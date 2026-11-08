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
