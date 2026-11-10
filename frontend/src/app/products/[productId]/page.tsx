"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { RequireActive } from "@/components/Account";
import { describeLocation, FLAG_TEXT } from "@/lib/location";
import type { Observation, ProductDetail } from "@/lib/types";
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
  return (
    <div className="stack">
      <p>
        <Link href={`/batches/${data.batch_id}`}>← Batch</Link>
      </p>
      <div className="title-row">
        <h1>{data.product_key}</h1>
        <span className="badge">{data.status}</span>
      </div>
      <p className="muted">
        Product {data.source_order} in file order.
        {data.supplier_sku !== null &&
          data.supplier_sku !== data.product_key &&
          ` Written in the file as "${data.supplier_sku}".`}
      </p>
      {data.identifier_flags.map((flag) => (
        <p key={flag} className="error">
          {FLAG_TEXT[flag] ?? flag}
        </p>
      ))}
      <h2>What the files say</h2>
      <p className="muted">
        Each value is exactly as written in the file. Where the files give
        more than one value for an attribute, every one is listed and marked
        &ldquo;differs&rdquo;; none is chosen here.
      </p>
      <ObservationTable
        observations={data.observations}
        differing={new Set(data.differing_attributes)}
      />
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
