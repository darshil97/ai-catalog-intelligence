"use client";

import type { BatchImage, BatchImageList, ImageMatchList } from "@/lib/types";
import { useApiResource } from "@/lib/useApiResource";

/**
 * A batch's kept images, with images that show the same picture placed together. A group is
 * shown, not acted on: every image is kept, and matching images to products comes later.
 */
export function ImageGrid({
  batchId,
  version,
}: {
  batchId: string;
  version: number;
}) {
  const images = useApiResource<BatchImageList>(`/batches/${batchId}/images`);
  // Reloaded after each review decision, through the version in its path.
  const matches = useApiResource<ImageMatchList>(
    `/batches/${batchId}/matches?v=${version}`,
  );
  const shows = new Map<string, string[]>();
  for (const match of matches.data?.items ?? []) {
    if (match.decision === "auto_accepted" || match.decision === "accepted") {
      shows.set(match.image_id, [
        ...(shows.get(match.image_id) ?? []),
        match.product_key,
      ]);
    }
  }
  const items = images.data?.items ?? [];
  if (images.error) {
    return (
      <p className="error" role="alert">
        {images.error.message}
      </p>
    );
  }
  if (items.length === 0) return null;

  // Each group is shown where its first image falls in file order, with its members together.
  const placed = new Set<string>();
  const ordered: BatchImage[] = [];
  for (const image of items) {
    if (placed.has(image.id)) continue;
    const members = image.duplicate_group_id
      ? items.filter((other) => other.duplicate_group_id === image.duplicate_group_id)
      : [image];
    members.sort(
      (a, b) => Number(b.is_representative) - Number(a.is_representative),
    );
    for (const member of members) {
      placed.add(member.id);
      ordered.push(member);
    }
  }
  const groups = new Set(
    items.map((image) => image.duplicate_group_id).filter(Boolean),
  ).size;

  return (
    <section className="section">
      <h2>Images</h2>
      <p className="muted">
        {items.length} images.
        {groups > 0
          ? ` ${groups} ${groups === 1 ? "group shows" : "groups show"} the same picture more than once; each group is outlined, largest first. Nothing was removed.`
          : " No image repeats another."}
      </p>
      <ul className="image-grid">
        {ordered.map((image) => (
          <li
            key={image.id}
            className={image.duplicate_group_id ? "grouped" : undefined}
          >
            {image.thumbnail_url ? (
              // Signed links to private storage: next/image can't optimise them.
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={image.thumbnail_url}
                alt={image.source_file_name}
                loading="lazy"
              />
            ) : (
              <span className="thumb-missing">No preview</span>
            )}
            <span className="image-name">
              {image.parent_file_name ? `${image.parent_file_name} › ` : ""}
              {image.source_file_name}
            </span>
            <span className="muted">
              {image.width && image.height
                ? `${image.width} × ${image.height}`
                : ""}
              {image.duplicate_group_id && !image.is_representative
                ? ` · same picture as ${image.group_size - 1} other${image.group_size > 2 ? "s" : ""}`
                : ""}
            </span>
            <span className={shows.has(image.id) ? undefined : "muted"}>
              {shows.has(image.id)
                ? `Shows: ${shows.get(image.id)!.join(", ")}`
                : "No product yet"}
            </span>
            {image.skip_message && (
              <span className="error file-reason">{image.skip_message}</span>
            )}
            {image.embedding_note && (
              <span className="muted file-reason">{image.embedding_note}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
