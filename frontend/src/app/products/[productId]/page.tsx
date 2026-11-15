"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { RequireActive } from "@/components/Account";
import {
  describeLocation,
  FLAG_TEXT,
  VALUE_FLAG_TEXT,
} from "@/lib/location";
import type {
  CatalogValue,
  Observation,
  ProductCategory,
  ProductDetail,
} from "@/lib/types";
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
      <CategoryLine category={data.category} />
      <h2>Catalog values</h2>
      <p className="muted">
        Each value in standard units, shown only when every source agrees.
        Disagreements wait for you in the batch&apos;s review list.
      </p>
      <CatalogTable values={data.catalog_values} />
      {data.images.length > 0 && (
        <>
          <h2>Photos</h2>
          <ul className="photo-strip">
            {data.images.map((image) => (
              <li key={image.image_id}>
                {image.thumbnail_url ? (
                  // Signed links to private storage: next/image can't optimise them.
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={image.thumbnail_url} alt={image.image_name} />
                ) : (
                  <span className="thumb-missing">No preview</span>
                )}
                <span className="muted">
                  {image.image_name}
                  {image.decision === "accepted"
                    ? " · confirmed by you"
                    : " · its code is on it"}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
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

const STATE_TEXT: Record<CatalogValue["state"], string> = {
  verified: "sources agree",
  conflict: "sources disagree",
  unclear: "needs a person",
  missing: "missing",
  reviewer_set: "set by you",
};

function CategoryLine({ category }: { category: ProductCategory | null }) {
  if (!category || category.state === "none") return null;
  if (category.state === "needs_review") {
    return (
      <p className="muted">
        Category: waiting for you to choose
        {category.suggested ? ` (suggested: ${category.suggested.name})` : ""}.
      </p>
    );
  }
  return (
    <p>
      Category: <strong>{category.category?.name}</strong>{" "}
      <span className="muted">
        {category.source === "person"
          ? "· chosen by you"
          : `· chosen by the LLM, ${Math.round((category.confidence ?? 0) * 100)}% sure`}
      </span>
    </p>
  );
}

function CatalogTable({ values }: { values: CatalogValue[] }) {
  const shown = values.filter((value) => value.state !== "missing");
  const missing = values.filter((value) => value.state === "missing");
  return (
    <>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Attribute</th>
              <th scope="col">Value</th>
              <th scope="col">From the sources</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((value) => (
              <tr key={value.code}>
                <td>
                  {value.name}{" "}
                  <span
                    className={
                      value.state === "conflict" || value.state === "unclear"
                        ? "badge warning"
                        : "badge"
                    }
                  >
                    {STATE_TEXT[value.state]}
                  </span>
                </td>
                <td className="value">{value.shown ?? "—"}</td>
                <td>
                  <ul className="evidence">
                    {value.values.map((source) => (
                      <li key={source.id}>
                        {source.display} from &ldquo;{source.raw_value}&rdquo;,{" "}
                        {source.source_file_name}
                        {source.flags.length > 0 &&
                          ` (${source.flags.map((f) => VALUE_FLAG_TEXT[f] ?? f).join(", ")})`}
                      </li>
                    ))}
                  </ul>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {missing.length > 0 && (
        <p className="muted">
          No source gave: {missing.map((value) => value.name).join(", ")}.
        </p>
      )}
    </>
  );
}
