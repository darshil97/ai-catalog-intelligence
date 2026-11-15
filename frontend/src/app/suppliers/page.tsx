"use client";

import Link from "next/link";

import { RequireActive } from "@/components/Account";
import { NameForm } from "@/components/NameForm";
import { api, jsonBody } from "@/lib/api";
import type { Supplier, SupplierList } from "@/lib/types";
import { useApiResource } from "@/lib/useApiResource";

export default function SuppliersPage() {
  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Suppliers</h1>
          <p className="sub">
            Each supplier holds batches: one delivery of files to read, review and export.
          </p>
        </div>
      </div>
      <RequireActive>
        <Suppliers />
      </RequireActive>
    </main>
  );
}

function Suppliers() {
  const { data, error, reload } = useApiResource<SupplierList>("/suppliers");
  const suppliers = data?.items ?? null;

  async function create(name: string) {
    await api<Supplier>("/suppliers", jsonBody("POST", { name }));
    reload();
  }

  return (
    <div className="stack">
      <div className="card">
        <h2>Add a supplier</h2>
        <NameForm label="Supplier name, such as Acme Kitchenware" submitLabel="Add supplier" onSubmit={create} />
      </div>
      {error && (
        <p className="error" role="alert">
          {error.message}
        </p>
      )}
      {suppliers === null && !error && <p className="muted">Loading…</p>}
      {suppliers?.length === 0 && (
        <p className="empty">No suppliers yet. Add your first one above.</p>
      )}
      {suppliers && suppliers.length > 0 && (
        <ul className="cards">
          {suppliers.map((supplier) => (
            <li key={supplier.id}>
              <Link href={`/suppliers/${supplier.id}`} className="tile">
                <span className="logo" aria-hidden="true">
                  {supplier.name.slice(0, 2).toUpperCase()}
                </span>
                <h3>{supplier.name}</h3>
                <span className="tile-foot">
                  <span>
                    {supplier.batch_count} {supplier.batch_count === 1 ? "batch" : "batches"}
                  </span>
                  <span aria-hidden="true">→</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
