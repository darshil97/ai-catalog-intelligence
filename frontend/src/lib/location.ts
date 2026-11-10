import type { CellLocation } from "@/lib/types";

/**
 * Describes a place in a file for people: "Sheet1, row 14, column C", "page 2" or "photo, read
 * by OCR".
 */
export function describeLocation(location: CellLocation | null): string {
  if (!location) return "";
  const parts: string[] = [];
  if (location.sheet) parts.push(location.sheet);
  if (location.row !== undefined) parts.push(`row ${location.row}`);
  if (location.column) parts.push(`column ${location.column}`);
  if (location.page) parts.push(`page ${location.page}`);
  if (location.page === null && location.method) parts.push("photo");
  if (location.method === "ocr") parts.push("read by OCR");
  return parts.join(", ");
}

/** Explains an identifier problem the worker flagged. */
export const FLAG_TEXT: Record<string, string> = {
  scientific_notation:
    "This code was saved in scientific notation, so some of its digits are missing in the file.",
  precision_lost:
    "This number is longer than a spreadsheet can store, so its last digits were lost in the file.",
  invalid_gtin: "This isn't a valid barcode number.",
};
