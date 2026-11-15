# M10 Export — proposal

**Status:** awaiting the owner's approval. Nothing is built yet.
**Written:** 2026-11-15, against `main` at `6356391` (M9 shipped; `production` at migration `0012`).
**Builds on:**
- M9: `approved_products()`, the only door to export.
- M6: variant pairs.
- M5B: accepted photos.
- M7: copy.
- M8: marketplace profiles.

**Also in this module (owner's request):** a minimalist animation wherever work is in progress.

---

## 1. Objective

Turn a batch's approved products into files a seller can use:
- a **CSV**;
- an **Excel workbook** that also shows where every value came from;
- a **Shopify product CSV** that imports cleanly, with photos and colour or size variants.

Only products that are approved, and unchanged since, are exported. Across the app, anything still working shows that it is: a quiet animated indicator instead of a static message.

## 2. Scope (IMPLEMENTATION_PLAN M10, plus the animation)

**In scope**
- The three formats, built from approved products only.
- A presigned download link per export, and a list of a batch's past exports.
- **Spreadsheet safety:** any cell starting with `=`, `+`, `-`, `@`, a tab or a carriage return is made inert.
- **Variants:** "These are variants" and "Not variants" on the batch's possible-variant pairs (the states exist since M6), and confirmed pairs grouped under one Shopify product.
- **Photo links** in the Shopify file, so Shopify can fetch the photos while importing.
- **Progress animation** wherever processing, copy writing, photo reading, exporting or saving is underway.

**Out of scope**
- Pushing to Shopify or Amazon through their APIs (excluded from the MVP).
- An Amazon flat file. The Amazon India profile checks products, but its export template is large and category-specific; the CSV and Excel files cover it for now.
- Stock levels, tax settings and inventory. The Shopify file leaves them at Shopify's defaults.

## 3. Acceptance criteria (from the plan)

1. Exports contain approved records only: approved, and unchanged since approval. A test approves a product, changes it, and confirms it's left out.
2. The Shopify file imports cleanly: exactly Shopify's column names and order, one row per photo, variants grouped under one handle, valid prices and weights. This is checked against Shopify's current template (see 4.4), and in your live test by importing it into a Shopify store (a free development store works).
3. Formula injection is impossible in every format.
4. Variants and photo links come out correctly.
5. Cross-user, signed-out and waitlisted access is refused on every new endpoint.

## 4. How it works

### 4.1 Built by the API, on request (decision 1)

The plan said the worker generates exports. **I recommend the API builds them instead, on request, and stores each file in S3:**

- **It's instant.** A batch holds at most 5 products, so a file takes milliseconds to build. The worker would first need about a minute to boot EC2.
- **Photo links have to last.** Shopify fetches each photo from its link while importing. Our photos are private, so links must be signed. Signed links last as long as the credentials that sign them: the worker's are temporary instance-role credentials (hours), while the API's key can sign links for S3's 7-day maximum.
- **It's cheap.** No LLM, no EC2. The API gains `openpyxl` for the Excel file (about 10 MB of the 512 MB Render limit), and the memory test keeps watching that.

D14 placed export generation in the worker, so this is recorded as a change (D41). If batches grow large later, the same builder can run as a worker job without changing.

### 4.2 The three formats (decision 2)

**CSV:** one row per product.
- Identity: code, name and brand.
- Category: the full path.
- Every attribute's settled value, in standard units, one column each.
- Copy: title, description, and bullet 1 to bullet 5.
- Photos: the photo links, joined with ` | `.
- Approval: approved at and by.

The file is UTF-8 **with** a byte-order mark, so Excel shows "₹" and Hindi text correctly.

**Excel:**
- **Products sheet:** the same columns as the CSV.
- **Sources sheet:** one row per value, with the file, sheet, row and column, or PDF page and quote, that it came from, or "Set by you". This makes the provenance the product promises visible to whoever receives the file.
- **About sheet:** the batch, the marketplace profile, when it was made, and how many products were included and left out.

**Shopify CSV:** Shopify's product import format.
- **One product row,** then one extra row per additional photo, with the same `Handle`.
- **Handle:** from the title, lowercase and hyphenated; the product code if needed to keep it unique.
- **Columns:**
  - Title: the copy's title.
  - Body (HTML): the description, then the bullets as a list.
  - Vendor: the brand.
  - Product Category: left blank (see 4.4).
  - Type: the last part of our category.
  - Tags: our category path.
  - Status: `draft`, so nothing goes live by accident (decision 5).
  - Variant SKU and Variant Barcode: the product code and barcode.
  - Variant Price: the price in rupees, as a plain number.
  - Variant Grams: the net weight.
  - Image Src, Image Position and Image Alt Text: one row per photo.
- **Variants:** see 4.3.
- **Encoding:** plain UTF-8, without a byte-order mark, as Shopify expects.

**Products left out:** each file's About sheet (Excel) or the export's summary on screen lists the batch's other products and why they weren't exported, such as "not approved" or "changed since approval".

### 4.3 Variants (decision 3)

- **Confirming pairs:** the batch's "Possible variants" list gains **These are variants** and **Not variants**. Each decision is recorded in history and makes approval out of date for both products.
- **Grouping in Shopify:** confirmed pairs (and chains of them, such as red, blue and green) become **one Shopify product** with one row per variant. `Option1 Name` is the attribute that differs between them: "Colour" if their colours differ, "Size" for capacity or size, otherwise "Style". `Option1 Value` is each product's value of it.
- **Shared fields:** the group's title, description, vendor and type come from its first product, with the colour or size word taken out of the title. The rest are per variant: SKU, price, barcode, grams and the variant's own photo.
- **When grouping isn't possible:** if the products differ in more than one attribute, they're exported as separate products, and the export says so.
- **CSV and Excel:** a "Variant group" column links grouped products. Nothing is merged.

### 4.4 Shopify's format is checked, not remembered (decision 4)

My knowledge of Shopify's column names comes from memory. Before building the Shopify file, I'll read Shopify's current help page for its product CSV template, and match the column names and order exactly. I'll put the source in a code comment and the docs. That means reading a public web page, with no account and nothing downloaded.

`Product Category` uses Shopify's own taxonomy, not ours, so it's left blank and our path goes in `Type` and `Tags`. That's safe, because Shopify accepts it blank.

### 4.5 Photo links (decision 5)

- **Which photos:** the photos accepted for each product (M5B), largest first, re-signed for 7 days whenever an export is made.
- **On screen:** the export says "Photo links work until 22 Nov. Import before then, or export again."
- **In CSV and Excel:** the same links, so a person can open the photos too.
- **Status draft:** imported Shopify products are drafts, to be reviewed in Shopify before publishing.

### 4.6 Spreadsheet safety (decision 6)

In CSV and Excel, any text cell starting with `=`, `+`, `-`, `@`, a tab or a carriage return gets a leading apostrophe. Excel shows the text as typed and never runs it as a formula. Numbers stay numbers.

The Shopify file is imported, not opened in a spreadsheet, so a leading apostrophe would end up in the product's title. There, those leading characters are removed from text fields instead, and the export lists any field it changed.

### 4.7 Downloads and history (decision 7)

- **What an export records:** each export is stored under `{env}/exports/{owner}/{batch}/` and recorded in `exports` with its format, products, profile, who made it, and when its photo links expire.
- **Download links:** a link is signed fresh, for 15 minutes, on each click.
- **History:** the batch page lists past exports.
- **Retention:** exports go with their batch, and S3's 31-day expiry removes the files.

### 4.8 Progress animation (decision 8)

One small component, `Working`, used everywhere something is underway. It's a thin indeterminate bar that slides in the accent colour, with a pulsing dot beside the label (such as "Processing… reading files", "Writing copy…", "Reading photos…", "Building your Shopify file…"). Buttons that are busy show a small spinner in place of their arrow.

**Where it appears:**
- the batch's run panel while queued or processing, with the current stage;
- waiting for the worker to start;
- copy being written, on the Copy tab, the next-step card and product rows;
- confirmed photos being read;
- review decisions saving;
- exports building;
- every busy button.

It's minimal: no spinners filling the page and no layout shift. It honours "reduce motion": with that setting on, the bar is still and only the label shows. Where a stage reports progress (chunks done of total), the bar shows the real share instead of sliding.

## 5. Schema — migration `0013`, add-only

**I'll ask before applying it to `test` or `dev`. CI applies it to `production` on ship.**

`exports` (the planned table, built now):

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `batch_id`, `owner_user_id` | FK | Cascade |
| `format` | TEXT NOT NULL | `csv`, `xlsx`, `shopify_csv` |
| `status` | TEXT NOT NULL | `ready`, or `failed` with `error` |
| `s3_key` | TEXT | The file |
| `profile` | TEXT NOT NULL | The marketplace profile then |
| `product_ids` | UUID[] NOT NULL | Exactly which products went in |
| `product_count`, `left_out_count` | INTEGER NOT NULL | |
| `links_expire_at` | TIMESTAMPTZ | When the photo links stop working |
| `size_bytes`, `sha256` | | Of the file |
| `created_by`, `created_at` | | |

Variant decisions reuse `variant_candidates.status` (`confirmed`, `dismissed`) and `review_events`. No schema change is needed for them.

## 6. API

| Endpoint | Does |
|---|---|
| `POST /batches/{id}/exports` | Body `{format}`. Builds the file from approved products and returns the export with a download link. 422 `nothing_approved` when none qualify. |
| `GET /batches/{id}/exports` | Past exports, newest first |
| `GET /exports/{id}/download` | A fresh 15-minute link |
| `POST /variants/{id}/decision` | Body `{decision: "confirmed" \| "dismissed"}`. Recorded in history. |

## 7. Frontend

- **Batch page:**
  - an **Export** tab with three format cards (what's in each, and how many products will go in), each with a Download button;
  - the past exports;
  - the photo-link expiry notice;
  - "These are variants" and "Not variants" on possible variants.
- **Next-step card:** after approval it offers "Export N approved products", then "Exported".
- **Animation:** `Working` across the batch page, the product page, the review page and every busy button.

## 8. Cost

No LLM and no EC2. Each export stores a file of a few KB in S3, removed after 31 days.

## 9. Tests

**Locally:** M10's new tests plus the fast tests. DB tests run on Neon `test`, and CI runs the full suite.

| Area | Cases |
|---|---|
| Formula safety (fast) | `=SUM(1)`, `+1`, `-1`, `@x`, a tab and a carriage return, in CSV and Excel cells. Shopify strips them and lists the change. Numbers stay numbers. |
| CSV and Excel (fast) | Columns, the UTF-8 byte-order mark, every attribute column, the Sources sheet listing each value's origin, and the About sheet listing what was left out and why. |
| Shopify (fast) | The exact header (from the checked template), handles unique, one row per photo with positions, HTML body with bullets, price and grams as numbers, status draft, and plain UTF-8. |
| Variants (fast) | A confirmed red and blue pair becomes one handle, two variant rows and `Option1 Name` = Colour. A dismissed pair stays two products. A pair differing in two attributes stays separate, with a note. |
| Approved only (DB) | Approve, export, change the product, export again: it's left out and listed as changed. With nothing approved, 422. |
| Downloads (DB) | Link signed fresh each time, export history, and the file in the in-memory store matching its recorded hash. |
| Variant decisions (DB) | Confirm and dismiss are recorded in history and break the approval. |
| API (DB) | Cross-user, signed-out and waitlisted on every endpoint. |
| Boundary | The API still loads within its memory guard with `openpyxl`. |

## 10. Decisions for approval

Each will be recorded as part of **D41** once approved.

| # | Decision | Recommendation |
|---|---|---|
| 1 | Who builds exports | **The API, on request,** stored in S3. Instant, and only the API can sign photo links for 7 days. This replaces the plan's worker job and changes D14. |
| 2 | Formats | **CSV (UTF-8 with a byte-order mark), Excel with Products, Sources and About sheets, and Shopify CSV (plain UTF-8).** |
| 3 | Variants | **Add "These are variants" and "Not variants".** Confirmed pairs become one Shopify product, with the option named after the attribute that differs. |
| 4 | Shopify's format | **Checked against Shopify's current help page** before building (reading a public page), not taken from memory. Product Category left blank. |
| 5 | Photos and status | **Accepted photos, linked for 7 days, and Shopify products imported as `draft`.** |
| 6 | Spreadsheet safety | **A leading apostrophe in CSV and Excel; risky leading characters removed in the Shopify file,** with the change listed. |
| 7 | Downloads | **A 15-minute link signed on each click, a history of exports per batch,** and files removed with the batch or after 31 days. |
| 8 | Progress animation | **One `Working` indicator (a thin sliding bar with a pulsing dot), real progress where stages report it, still when "reduce motion" is on,** used wherever anything is underway. |

## 11. Process from here

1. You approve or amend the decisions above.
2. I read Shopify's current CSV help page, then implement on a branch and run local checks. **I'll ask before applying `0013` to `test`.**
3. I ask before shipping, and CI migrates `production` on merge.
4. Your live test:
   - approve the Vardhan products;
   - confirm the red and blue bottles as variants;
   - download all three files;
   - open the CSV and Excel files;
   - import the Shopify file into a Shopify development store.
5. Log check, then I mark M10 Completed, which completes the MVP plan.
