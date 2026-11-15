# M8 Catalog validation — proposal

**Status:** awaiting the owner's approval. Nothing is built yet.
**Written:** 2026-11-15, against `main` at `8f0b2d0` (M7 shipped; `production` at migration `0010`).
**Builds on:** M6 (attribute states, shown values, categories, variant pairs), M5B (accepted photos, review and audit trail), M7 (generated text and whether it is out of date).
**Leads to:** M9 (approval: products with errors can't be approved) and M10 (export: what each marketplace file needs).

---

## 1. Objective

Check every product against a small set of rules and say, in plain words, what is missing, conflicting, suspicious or duplicated before anyone approves or exports it. The checks are deterministic code with no LLM. Rules live in a registry and their settings in `config.yaml`. A marketplace profile (Shopify, Amazon India, or generic) adds that marketplace's requirements. Nothing is fixed automatically: a check only reports.

## 2. Scope (from IMPLEMENTATION_PLAN M8)

**In scope**
- A rule registry in `app/validation/rules/`, with one module per rule, each with a code, version, default severity and description.
- Marketplace profiles in config, and a profile chosen per batch.
- Duplicate detection across the products of one batch.
- Issues shown on the batch page and the product page. A person can set a **warning** aside with a reason, and that is recorded in the audit trail.
- The function M9 calls to decide whether a product can be approved, and M10 calls before exporting.

**Out of scope**
- Fixing anything automatically, or merging duplicates.
- Duplicates across batches or users (D20's rule that comparisons never cross users still holds).
- Approving products and bulk actions (M9). Export files (M10).
- Any LLM call. M8 costs nothing per product.

## 3. Acceptance criteria

1. Missing, conflicting and unclear values are flagged per attribute, with the same reasons M6 and M7 already give.
2. Duplicates within a batch are flagged in pairs, with the evidence, and never merged.
3. Rules, severities and marketplace requirements come from config. Changing a severity or a required attribute needs no code change. An unknown rule name in config stops the app at startup.
4. Every issue names its rule, rule version, severity, product and, where it applies, the attribute or other product.
5. Issues are always current: a review decision, a regenerated text or an accepted photo shows up the next time the page loads.
6. A warning set aside stays aside until what it was about changes. An error can't be set aside.
7. Cross-user, signed-out and waitlisted access are tested on every new endpoint.

## 4. How it works

### 4.1 Checked when read, not stored (decision 1)

The rules are pure functions in shared code (`app/validation`). They run over a batch's data each time the batch's checks, a product or (later) an approval or export is read. Only the warnings a person set aside are stored.

Why:
- **Always current, with no extra moving parts.** Review decisions happen in the API. A stored list would go stale until a worker job ran again, and starting EC2 for a check that takes milliseconds would cost a minute's boot.
- **Cheap.** A batch holds at most a few products (the POC limit is 5). One batch read is about 5 queries (≈ 0.5 s over the Singapore–Sydney link), the same as the M7 panel.
- **The worker and API agree,** because both call the same function. M10's export checks again just before writing.

This changes one line of PROJECT_CONTEXT's API and worker table, which says validation runs in the worker. Rules are business logic, not file parsing or inference, so the API boundary (no files, no models) still holds. The import-boundary test proves it.

If batches grow large later, the same functions can run in a worker stage and store their results. That needs no rule changes.

### 4.2 Severities (decision 2)

| Severity | Meaning | Effect |
|---|---|---|
| `error` | The product isn't fit to list | M9 refuses to approve it. It can't be set aside, only fixed. |
| `warning` | Worth a look, but may be fine | Shown, and doesn't block. It can be set aside with a reason. |
| `info` | Good to know | Shown, never blocks |

A rule's severity can be overridden per profile in config. For example, a missing photo is a warning for Shopify and an error for Amazon India.

### 4.3 Marketplace profiles (decision 3)

```yaml
validation:
  rules_version: checks@1
  default_profile: shopify
  profiles:
    generic:     { required: [product_name, brand], title_max_chars: 200, ... }
    shopify:     { required: [product_name, price], title_max_chars: 255, photo: warning, ... }
    amazon_in:   { required: [product_name, brand, gtin, price], title_max_chars: 200,
                   bullets_min: 3, photo: error, ... }
```

- A batch has one profile: `batches.target_marketplace`, defaulting to `shopify` because M10 exports a Shopify file. It can be changed on the batch page at any time. Checks are recomputed, so changing it is free.
- **Shopify is the default** because it's the only marketplace file M10 produces. Amazon India is included because the users are Indian, but nothing exports to it yet.
- **The marketplace numbers are from memory, not checked against current Shopify or Amazon documentation.** They're marked unverified in config. Before relying on a profile, check it against the marketplace's current rules (decision 8).

### 4.4 The rules

Each rule is `check(ctx) -> list[Issue]`, where `ctx` holds the batch's products, settled attributes, categories, generated text, accepted photos, variant pairs and the profile.

| Rule | Checks | Default severity |
|---|---|---|
| `required_attribute` | Each attribute the profile requires has a settled value. Missing, conflict and unclear are reported separately, with M6's reasons ("Sources disagree; waiting for review"). | error |
| `attribute_conflict` | Any other attribute in conflict | warning |
| `attribute_unclear` | Any other attribute unclear (no unit, a range, …) | info |
| `category` | A settled category: assigned by the LLM, or chosen by a person | error |
| `identifier` | The product's code flags from M3: invalid GTIN check digit, scientific notation, precision lost | error |
| `price` | A settled price above zero. A currency assumed from no symbol or from "$" (M6) is reported. | error (≤ 0), warning (assumed currency) |
| `measurements` | Settled weights and sizes are above zero and under a sanity ceiling from config (for example 1,000 kg, 5 m). This catches a unit mistake such as "1500 kg" for a jug. | warning |
| `generated_text` | Text exists and is current. Missing, failed or out of date are reported (M7). | warning |
| `title_length` | The title (generated, or the product name if none) fits the profile's limit | error |
| `bullets` | Generated bullets meet the profile's minimum, where it has one | warning |
| `photo` | At least one accepted photo (M5B) | profile decides |
| `open_review` | Open review tasks on the product: image match, conflict, category | warning |

**Duplicates within the batch** (decision 5). These are flagged on both products, never merged:

| Rule | Two products that… | Severity |
|---|---|---|
| `duplicate_gtin` | share the same valid GTIN (settled) under different product keys | error |
| `duplicate_model_number` | share the same model number | warning |
| `duplicate_name` | have the same brand and the same name, ignoring case and spacing, and aren't a flagged variant pair | warning |
| `duplicate_photo` | have the same accepted photo, or near-duplicates of one (M5A groups) | warning |

A pair a person confirmed as variants in M6 isn't reported as a duplicate. A pair they dismissed isn't treated as variants. Duplicate products differ from variants: variants differ in colour or size (M6), while duplicates look like the same product entered twice.

### 4.5 Setting a warning aside (decision 6)

- **What a person can do:** set a warning aside with a short reason ("This supplier never gives brands"). Info needs nothing, and errors can't be set aside.
- **What's stored:** the rule, its subject (the attribute or the other product) and a **fingerprint** of what the check saw. If that changes, for example a different value or a new photo, the warning comes back.
- **Audit:** each set-aside and undo is recorded in `review_events` with the person and the before and after.

## 5. Schema — migration `0011`, add-only

**I'll ask before applying it to `test` or `dev`. CI applies it to `production` on ship.**

**`validation_overrides`** (new): the warnings set aside. The planned `validation_issues` table isn't created, because issues aren't stored (decision 1).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `product_id`, `batch_id`, `owner_user_id` | FK | Cascade |
| `rule_code` | TEXT NOT NULL | Such as `duplicate_name` |
| `subject` | TEXT NOT NULL | The attribute code, the other product's ID, or `''` |
| `fingerprint` | TEXT NOT NULL | What the check saw. The override applies only while it matches. |
| `reason` | TEXT NOT NULL | 1–500 characters |
| `created_by` | TEXT NOT NULL | The person |
| `created_at` | TIMESTAMPTZ | |

Constraint: `UNIQUE(product_id, rule_code, subject)`. Setting a warning aside again replaces the row.

**`batches.target_marketplace`**: TEXT NOT NULL DEFAULT `'shopify'`. Checked in code against the profiles in config, so adding a profile needs no migration.

## 6. Code layout

| Path | What |
|---|---|
| `app/validation/base.py` | `Issue`, `Rule` protocol, registry, `Severity` |
| `app/validation/context.py` | Loads one batch's data for the rules (shared; about 5 queries) |
| `app/validation/rules/*.py` | One module per rule |
| `app/validation/engine.py` | Runs the profile's rules, applies severity overrides and set-asides, sorts issues |
| `app/validation/repository.py` | Set-aside reads and writes, with the audit record |
| `app/templates/sql/validation.sql` | Queries |
| `config.yaml` | `validation:` section, checked at startup (unknown rule names, unknown attributes) |

## 7. API

| Endpoint | Does |
|---|---|
| `GET /batches/{id}/validation` | Per product: errors, warnings, info, set-aside count, and whether it can be approved. Batch totals and the profile. |
| `PATCH /batches/{id}` | Gains `target_marketplace` (one of the configured profiles; 422 otherwise) |
| `GET /products/{id}` | Adds `validation`: the product's issues, each with rule, severity, message, subject and whether it was set aside (with who, why and when) |
| `POST /products/{id}/validation/set-aside` | Body `{rule_code, subject, reason}`. 422 if it isn't a current warning. |
| `DELETE /products/{id}/validation/set-aside?rule_code=…&subject=…` | Undo |

## 8. Frontend (minimal)

- **Batch page, "Checks" panel:** a marketplace selector, then one row per product, such as "AK-1001: 2 errors, 1 warning", errors first, with a link to the product. A one-line batch summary: "3 of 5 products ready for Shopify".
- **Product page, "Checks" section:** issues grouped by severity, each in plain words with a link to fix it where there is one (the review panel, Generate). Warnings have a "Set aside" button that asks for a reason. Set-aside warnings appear greyed out, with an Undo.

## 9. Cost

No LLM and no EC2. Each batch or product read adds about 5 queries, about 0.5 s. Neon storage is one small row per set-aside warning.

## 10. Tests

**Locally:** M8's new tests plus the fast tests. Rule tests need no database. DB tests run on Neon `test`. CI runs the full suite.

| Area | Cases |
|---|---|
| Registry (fast) | Every rule has a unique code, a version, a severity and a description. An unknown rule or attribute in config fails at startup. A severity override from a profile applies. |
| Each rule (fast) | A passing and a failing case each. Required attributes when missing, in conflict or unclear. Price ≤ 0 and an assumed currency. A unit mistake caught by the ceiling. Title over the limit, with and without generated text. Out-of-date text. Photo as a warning for Shopify and an error for Amazon India. |
| Duplicates (fast) | Same GTIN on different keys. Same name and brand. A flagged variant pair not reported. A confirmed variant pair not reported. Shared and near-duplicate photos. Sequential codes (AK-1003 and AK-1004) not reported. |
| Engine (fast) | Issues sorted errors first. A set-aside applies only while its fingerprint matches. Errors can't be set aside. `can_approve` is false with any error. |
| End to end (DB) | Process a batch with a missing brand, a conflict and a duplicate GTIN. Read its checks. Settle the conflict in review and the issue goes. Set a warning aside, change the value, and it comes back. Switch the profile and the issues change. The audit trail holds each set-aside and undo. |
| API (DB) | Cross-user, signed-out and waitlisted on every endpoint. 422 for setting aside an error or an unknown profile. |
| Boundary | The API still loads without worker packages, with `app/validation` imported. |
| Migration | `0011` on top of `0010` data. Existing batches get `shopify`. |

## 11. Edge cases

- **A batch with no products** (an image-only batch with no codes): no checks, and the panel says so.
- **A product a person deletes:** its set-asides go with it.
- **A deleted batch:** cascades.
- **A product with every value missing:** many issues, listed once each; the required-attribute rule doesn't repeat the conflict rule.
- **Profile changed after setting warnings aside:** set-asides keyed by a rule still apply. A warning that's now an error under the new profile is shown as an error, and the set-aside no longer counts.
- **A rule throwing on unexpected data:** that rule is reported as a `check_failed` info item naming the rule. The other rules still run, and nothing breaks the page.

## 12. Decisions for approval

Each will be recorded as part of **D38** once approved.

| # | Decision | Recommendation |
|---|---|---|
| 1 | Where checks run | **Shared pure rules, checked whenever read** (API and worker alike). Only set-aside warnings are stored, and the planned `validation_issues` table is replaced by `validation_overrides`. Not a worker stage that stores issues. |
| 2 | Severities | **error / warning / info.** Errors block approval in M9 and can't be set aside. Warnings can be set aside with a reason. |
| 3 | Marketplace profiles | **One profile per batch (`generic`, `shopify`, `amazon_in`), default `shopify`,** changeable any time, from config. |
| 4 | Required attributes | **Shopify:** name and price. **Amazon India:** name, brand, GTIN and price. **Generic:** name and brand. Plus a settled category for every profile. |
| 5 | Duplicates | **Within one batch only,** in pairs, never merged: same GTIN (error), same model number, same brand and name, same or near-duplicate photo (warnings). Variant pairs aren't duplicates. |
| 6 | Setting aside | **Warnings only, with a reason, until what was checked changes** (fingerprint), recorded in `review_events`. |
| 7 | Generated text | **Missing or out-of-date text is a warning.** A title over the profile's limit is an error, using the product name when there's no generated title. |
| 8 | Marketplace numbers | **Kept in config, marked unverified,** and checked against current Shopify and Amazon documentation before a profile is relied on. I can look them up with your OK. |
| 9 | Measurement ceilings | **Weight 1,000 kg, length 5 m, capacity 1,000 L,** as warnings, from config. |

## 13. Process from here

1. You approve or amend the decisions above.
2. I implement on a branch and run local checks: new tests plus fast tests, with DB tests on Neon `test`. **I'll ask before applying `0011` to `test`.**
3. I ask before shipping, and CI migrates `production` on merge.
4. Your live test: process a batch with a missing brand and a duplicate, read the checks, fix one in review, set a warning aside.
5. Log check, then I mark M8 Completed, with D38 and the glossary terms (check, severity, profile, set aside, duplicate).
