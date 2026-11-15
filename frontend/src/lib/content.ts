/** Words shared by the landing page and the how-it-works guide. */

export const PIPELINE = [
  { name: "Upload", text: "Spreadsheets, spec PDFs, photos, ZIPs" },
  { name: "Read", text: "Columns mapped, pages and photos read" },
  { name: "Match photos", text: "Each photo linked to its product" },
  { name: "Normalize", text: "Grams, millilitres, rupees; one value" },
  { name: "Write", text: "Titles and bullets that cite their values" },
  { name: "Check", text: "Missing, conflicting and duplicate data" },
  { name: "Review", text: "You decide anything unsure" },
  { name: "Export", text: "CSV, Excel and Shopify" },
];

export const FEATURES = [
  {
    icon: "✓",
    title: "Never invents a spec",
    text: "Every value points to the file, row, page or photo it came from. Generated copy that states something no value supports is refused by code.",
  },
  {
    icon: "◎",
    title: "Photos matched to products",
    text: "Codes printed on the box, file names and look-alikes are weighed together. Only a photo showing its own code is accepted without you.",
  },
  {
    icon: "₹",
    title: "Built for Indian suppliers",
    text: "Grams, millilitres and centimetres; rupees when no currency is given; 1,00,000 read the Indian way.",
  },
  {
    icon: "⇄",
    title: "Conflicts wait for you",
    text: "When the spreadsheet says 1.5 L and the PDF says 1.2 L, both are kept and nothing is chosen until you choose.",
  },
  {
    icon: "☰",
    title: "Ready for your marketplace",
    text: "Checks for Shopify or Amazon India: required fields, title lengths, photos and duplicate barcodes.",
  },
  {
    icon: "◷",
    title: "Small, honest limits",
    text: "Free during the preview, with clear limits per person and a monthly cap on AI spend.",
  },
];

export const STEPS = [
  {
    id: "batch",
    title: "Create a supplier and a batch",
    text: "A supplier is a company that sends you product data. A batch is one delivery of its files, such as “Spring price list”.",
    tip: "Use one batch per delivery, so its review list stays short.",
  },
  {
    id: "upload",
    title: "Upload the supplier's files",
    text: "Drop in spreadsheets (CSV or Excel), spec-sheet PDFs, product photos, or a ZIP of photos.",
    tip: "Files are checked in your browser first, so the same file isn't uploaded twice.",
  },
  {
    id: "process",
    title: "Process the batch",
    text: "Columns are mapped, PDFs and photos are read, photos are matched to products, and values are put into standard units. A five-product batch takes about a minute.",
    tip: "Processing uses one of your submissions. Reviewing, editing and exporting are free.",
  },
  {
    id: "review",
    title: "Decide what's unsure",
    text: "The review list shows photos to confirm, values where sources disagree, and categories the AI wasn't sure of, each with its evidence side by side.",
    tip: "Every decision is recorded with who made it and when.",
  },
  {
    id: "write",
    title: "Generate titles and bullets",
    text: "Copy is written only from settled values, and each line shows which values it uses. Anything unsettled is left out, not guessed.",
    tip: "Change a value later and the copy is marked out of date, ready to regenerate.",
  },
  {
    id: "check",
    title: "Check, then export",
    text: "Pick your marketplace and fix what the checks flag: errors must be fixed, warnings can be set aside with a reason. Then export approved products.",
    tip: "Exports to CSV, Excel and Shopify arrive in a coming update.",
  },
];

export const LIMITS = [
  "2 batches to process per account, at most 1 a day",
  "5 files per batch, 10 MB each and 25 MB in total",
  "PDFs up to 10 pages; up to 25 photos, including those inside ZIPs",
  "The first 5 products of each batch are kept; the rest are counted and reported",
  "Everything is deleted after 30 days",
];
