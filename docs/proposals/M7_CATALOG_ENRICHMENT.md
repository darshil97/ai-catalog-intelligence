# M7 Catalog enrichment — proposal

**Status:** awaiting the owner's approval. Nothing is built yet.
**Written:** 2026-11-15, against `main` at `99a4423` (M6 shipped; `production` at migration `0009`).
**Builds on:** M6 (`product_attributes` states and shown values, categories), the LLM runner from M3/M4 (`app/llm/runner.py`), and the review backbone from M5B.

---

## 1. Objective

For each product, generate a **title**, a **description** and **3–5 bullet points** from information the catalog has already settled. The text must follow the project's first rule: **never fabricate**. Every sentence and bullet names the attributes it relies on. Code checks those citations before anything is stored. An attribute with no settled value stays missing: the text doesn't mention it.

## 2. Scope (from IMPLEMENTATION_PLAN M7)

**In scope**
- Titles, descriptions and bullets from settled attributes, one gpt-oss-120b call per product, through the existing `run_task`, with a new `enrichment` task entry under `llm.tasks`.
- A prompt template at `app/templates/prompts/catalog/enrichment.md`.
- Citation and grounding checks in code (`app/enrichment/`), which reject unsupported text.
- `generated_content`, versioned, with citations frozen at the moment of generation.
- An `enrich` pipeline that runs on demand, with **`blocked_quota` built for the first time**: a run that reaches the spend cap pauses, and can be resumed later without losing anything.
- API, a batch-page panel and a product-page section (read-only).

**Out of scope (later modules)**
- Editing or approving generated text (M9). The `status` column is created now so that M9 needs no migration.
- Marketplace-specific length and wording rules (M8's ruleset). M7 uses fixed limits from config.
- Languages other than English.
- Photo-based copy (Claude Haiku). Text comes only from attributes.

## 3. Acceptance criteria

1. Every stored sentence, bullet and title cites one or more attributes. Each citation names a `verified` or `reviewer_set` attribute of that product, together with the value it had at generation time.
2. **Missing data stays missing.** A product without a settled capacity gets no capacity in its text. Tests prove this with products that lack fields, plus replies that try to add them.
3. Every call is in `model_runs` with model, prompt version, tokens, cost and latency. Each `generated_content` row points to its model run.
4. **The spend cap stops new calls.** A run that reaches the cap partway through is `paused`. Its unfinished jobs become `blocked_quota`, not `failed`. Products already done keep their text. Resuming finishes the rest, reuses stored replies and doesn't charge twice.
5. Malformed or truncated replies are retried once with feedback. If the reply is still unusable, the product shows "Not generated" with the reason, and the run keeps going.
6. Text goes "out of date" when the attributes it cites change, for example after a review decision. It is never regenerated silently.
7. Every new endpoint is tested cross-user, unauthenticated and waitlisted.
8. After M7 the owner can decide whether to open sign-ups (see §13).

## 4. Which attributes can feed the text

**Recommendation: `verified` and `reviewer_set` only**, as you suggested.

| State | Used? | Why |
|---|---|---|
| `verified` | Yes | Every reliable source agrees (D36) |
| `reviewer_set` | Yes | A person decided it |
| `conflict` | No | Using either value picks a winner, which D36 forbids |
| `unclear` | No | The value can't be relied on, for example a missing unit or a range |
| `missing` | No | Nothing to say |

The product's **category** is also a usable fact, but only when a person chose it or it is `assigned` (the LLM was sure and the category isn't "other"). It's cited as `category`, not as an attribute.

**Attributes never given to the model** (config `enrichment.excluded_attributes`):
- `price` and `currency`: marketplaces forbid prices in titles, and prices change.
- `sku`, `gtin` and `supplier_category`: internal codes.

`model_number` is allowed, but only in the title. That is the usual marketplace convention (decision 4).

The product page lists what was **left out and why**, for example: "Capacity: two sources disagree, waiting for review." That way a user knows to resolve reviews before generating.

**Too little to write about:** if a product has neither a usable `product_name` nor both `brand` and a category, it is marked `skipped` ("not enough verified information"). No call is made and nothing is charged.

## 5. Citations and catching fabrication

### 5.1 What the model sees

The prompt gets a numbered list of **facts**. Each fact holds a short ID, the attribute's name and its shown value (M6's `display`, for example `1.5 L` or `Stainless Steel`):

```
F1  Product name: Insulated water bottle
F2  Brand: TYGOT
F3  Capacity: 1.5 L
F4  Material: Stainless steel
F5  Category: Dining › Drinkware › Bottles
```

The supplier's own `description`, when verified, is passed as a fact inside a delimited data block. The prompt says it is data, not instructions (prompt injection, as in D33).

The prompt doesn't list missing attributes. Naming them invites "capacity not specified" filler. The checks below catch anything the model invents anyway.

### 5.2 What the model must return

```json
{
  "title":       {"text": "TYGOT Insulated Water Bottle, 1.5 L, Stainless Steel", "facts": ["F2","F1","F3","F4"]},
  "description": [{"text": "...", "facts": ["F1","F4"]}, ...],
  "bullets":     [{"text": "...", "facts": ["F3"]}, ...]
}
```

Each sentence of the description is its own **claim**, and so is the title and each bullet. Code joins the sentences into the description.

### 5.3 Checks in code (pydantic parser, then `app/enrichment/grounding.py`)

The reply is rejected if any claim fails one of these checks:

| # | Check | Catches |
|---|---|---|
| 1 | **Shape:** valid JSON. Title of at most 150 characters, 3–5 bullets of at most 200 each, 2–5 description sentences with at most 1,000 characters in total. Limits come from config. | Truncated or malformed output |
| 2 | **Has a citation:** every claim cites one or more fact IDs, and every ID exists in this prompt. | Uncited claims, invented IDs |
| 3 | **Citation is used:** each cited fact must show up in the claim. A text fact needs one of its words to appear (case and punctuation ignored). A number fact needs its quantity to appear. | Padding a claim with citations it doesn't use |
| 4 | **Numbers are grounded:** every number with a unit in the claim is read with M6's own unit rules (`normalization/values.py`) and must equal a cited fact's standard value within the 1% tolerance. So "1500 ml" and "1.5 L" both pass for F3, and "2 L" fails. A bare number, other than a count word such as "pack of 2" backed by `pack_quantity`, fails. | Invented or wrongly converted specifications, missing data that "came back" |
| 5 | **Claim words are grounded:** a config list of specification phrases must appear in a cited fact's text. Examples: dishwasher safe, microwave safe, BPA-free, leak-proof, food-grade, non-stick, rust-proof, waterproof, insulated, warranty, certified, ISO, FDA, organic, handmade, made in. The list is grown as live tests find gaps. | The classic fabrication ("dishwasher safe") |
| 6 | **Colour and material words:** a colour word (M6's `variants.colour_words`) or a word from a new short material list must appear in a cited fact. | "Black bottle" when colour is missing |
| 7 | **Banned phrases:** superlatives and promises ("best", "#1", "guaranteed", "100%", health claims), from config. | Marketing claims nobody can source |

What happens when a reply fails a check:
- `run_task` already retries once and tells the model exactly which claim failed and why (`max_attempts: 2`).
- If the second reply also fails, nothing is stored. The product's enrichment state becomes `failed` with the reasons, the job **succeeds**, and the run carries on. No review task is opened, because M7 has no way to edit text. M9 can add an `llm_output` task.
- Both failed calls are recorded in `model_runs` and count toward the cap.

**Honest limit:** code can't prove every adjective is harmless. "Sleek design" passes check 7. These checks make specification-style fabrication (numbers, materials, colours, safety and certification claims) impossible to store. They don't stop vague marketing language. Decision 5 asks whether to keep claims strict (recommended) or also require every content word to come from a fact, which would make the text wooden.

### 5.4 Citations stored

Citations are frozen. Each stored claim keeps `attribute_id`, `code`, the `display` value, the `state` at the time, and the `selected_value_id` or a reviewer flag. Text stays auditable even after values change. `input_fingerprint` (a hash of the facts given) marks text **out of date** when the current facts hash differently. That is worked out when the text is read, so no triggers are needed.

## 6. When enrichment runs, and the spend cap

### 6.1 On demand, as its own pipeline (recommended, decision 1)

```yaml
pipelines:
  enrich:
    - stage: enrich
      job_type: enrich_plan          # picks products, one chunk each
```

`enrich_plan` makes one `enrich_product` chunk per chosen product. Each chunk is one LLM call, so the cap can only be hit between products, never halfway through one.

**Why not a final stage of `ingest`:**
- After ingest, many attributes are still `conflict` or waiting for review. Text written then would leave them out and go out of date as soon as you decide them.
- A paused stage inside `ingest` would leave the batch "processing" until next month.
- Like `photo_values` (D35), an `enrich` run uses **no submission** and **doesn't change the batch's status**. It needs the batch to be `completed`.

The batch page's **Generate** button covers every product that has no text or out-of-date text. A product's own **Regenerate** button appears only when its text is out of date or failed. With the same facts, temperature 0 and stored-reply reuse, a regeneration would return the same text for free, so there is no "reroll" (decision 3).

### 6.2 Reaching the cap: pause, don't skip

Ingest's LLM steps *skip* when the cap is reached (D33). Enrichment *pauses* instead. Skipping would leave nothing useful, and pausing lets the work finish later at no extra cost.

```
enrich_product ──run_task──▶ LlmBudgetError
   │  handler raises QuotaBlockedError("llm_spend")
   ▼
queue.block(conn, job)  — one fenced transaction:
   • this job          → blocked_quota, attempt not counted (like release())
   • every pending job of the same run → blocked_quota
   • pipeline_run      → paused, pause_reason = 'llm_spend_cap'
   • stage stays running; finished products keep their content
```

- **Nothing is lost:** jobs stay in the table with their payloads. Claiming takes only `pending` jobs, so blocked ones are never picked up. The idle-shutdown check counts only `pending` and `running`, so EC2 stops normally.
- **A paused run doesn't hold your slot.** `paused` is outside the "one active run per user" index, so you can still process another batch.
- **Resume:** `POST /batches/{id}/enrichment/resume`. The API checks this month's spend against the cap with the same SQL the worker uses (moved into shared code). If spend is under the cap, in one transaction it sets the run to `running` and the blocked jobs to `pending`, then starts the worker. It returns 409 if another run of yours is active, and 409 `llm_budget_reached` if the cap is still reached. Nothing resumes automatically: there is no always-on scheduler, and the cap is a deliberate stop (decision 6).
- **No double billing on resume:** a resumed `enrich_product` first compares the product's current text fingerprint with its facts. If they match, it does nothing. Otherwise `run_task` reuses any stored reply before it checks the budget, which it already does today.
- **Starting a run when the cap is already reached:** `POST /batches/{id}/enrichment` refuses with 409 `llm_budget_reached`. No run is created just to pause it.
- **Known gap:** the cap is checked before each call, so the last call can go over it by about $0.001. This is already true for M3–M6.

## 7. Schema — migration `0010`, add-only

Nothing is dropped or renamed, and existing data is untouched. **I'll ask before applying it to `test` or `dev`. CI applies it to `production` on ship.**

### 7.1 `generated_content` (new; replaces the planned design)

The planned design had one row per field, with `UNIQUE(product_id, field, version)`. That can't hold several bullets per version. **I propose one row per product version** (decision 7):

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `product_id` | UUID FK | Cascade |
| `batch_id`, `owner_user_id` | FK | Cascade; ownership checks as elsewhere |
| `version` | INTEGER NOT NULL | 1, 2, … per product |
| `is_current` | BOOLEAN NOT NULL | Partial unique index on `(product_id) WHERE is_current` |
| `title` | TEXT NOT NULL | |
| `description` | TEXT NOT NULL | Sentences joined |
| `bullets` | TEXT[] NOT NULL | |
| `claims` | JSONB NOT NULL | `[{field, index, text, facts:[{attribute_id \| "category", code, display, state, selected_value_id, reviewer}]}]` |
| `cited_attribute_ids` | UUID[] NOT NULL | Union of the claims' citations, for queries |
| `input_fingerprint` | TEXT NOT NULL | SHA-256 of the facts given; used to detect out-of-date text |
| `rules_version` | TEXT NOT NULL | `enrich@1` (grounding rules) |
| `model_run_id` | UUID FK, nullable | The call; null for M9 edits |
| `status` | TEXT NOT NULL | `generated`, `edited`, `approved`, `rejected` (M9 uses the last three) |
| `created_by` | TEXT NOT NULL | `system` or a user ID |
| `created_at` | TIMESTAMPTZ | |

Constraint: `UNIQUE(product_id, version)`, plus a cap of `enrichment.max_versions_per_product` (10) enforced in code (decision 8).

### 7.2 Smaller additions

- `products`: `enrichment_state` (`none`, `generated`, `skipped`, `failed`; default `none`), `enrichment_message` TEXT and `enrichment_at` TIMESTAMPTZ. A paused product needs no state of its own, because its job is `blocked_quota`.
- `pipeline_runs`: `status` gains `paused` (the CHECK constraint is dropped and re-added as a superset), plus `pause_reason` TEXT. Old code never writes `paused`, so it stays compatible.
- `review_tasks`: no change in M7.

## 8. Code layout

| Path | What |
|---|---|
| `app/enrichment/facts.py` | Builds the fact list from `product_attributes` and the category, with exclusions and the fingerprint |
| `app/enrichment/grounding.py` | Checks 2–7 above; pure functions, no database |
| `app/enrichment/models.py` | Pydantic reply schema (check 1) |
| `app/jobs/handlers/enrich.py` | `enrich_plan`, `enrich_product` |
| `app/jobs/queue.py` + `errors.py` | `QuotaBlockedError`, `block()`, `resume_run()` |
| `app/templates/prompts/catalog/enrichment.md` | The prompt |
| `app/templates/sql/enrichment/*.sql` | Queries |
| `config.yaml` | `llm.tasks.enrichment` (max_tokens 4000, temperature 0, max_attempts 2, timeout 90 s); `enrichment:` limits, excluded attributes, claim words, banned phrases, material words; `pipelines.enrich` |

## 9. API

| Endpoint | Does |
|---|---|
| `POST /batches/{id}/enrichment` | Body `{product_ids?}`. Starts an `enrich` run for products with no text, out-of-date text or failed text. 409 for an active run, a batch that isn't `completed`, or `llm_budget_reached`. 422 if nothing needs generating. |
| `POST /batches/{id}/enrichment/resume` | Resumes the batch's paused run (§6.2) |
| `GET /batches/{id}/enrichment` | Per product: state, out-of-date flag, current version summary, open review count. Plus the last `enrich` run's status, its pause reason and blocked count. One query. |
| `GET /products/{id}` | Adds `generated_content`: current version with claims and frozen citations, `out_of_date`, and `left_out` (attribute, state, reason) |

The API never calls the LLM. Every endpoint gets cross-user, unauthenticated and waitlisted tests.

## 10. Frontend (minimal)

- **Batch page, "Generated content" panel:** a Generate button with the count it will cover and a note such as "3 attributes waiting for review will be left out". A row per product shows its state (Generated / Out of date / Not enough verified information / Couldn't generate: reason / Waiting: spend cap reached). When the run is paused, a banner offers Resume. Progress reuses the existing run polling.
- **Product page, "Generated content" section:** title, description and bullets. Each bullet and sentence has small chips for the facts it cites ("Capacity 1.5 L"). A "Left out" list gives the reasons. Regenerate appears only when the text is out of date or failed. No editing until M9.

## 11. Cost per product

Grounded on production `model_runs` (2026-11-15, read-only query): `document_attributes` averages 1,061 input and 635 output tokens, $0.00056 per call. `column_mapping` averages 1,612 and 322 tokens, $0.00045.

Enrichment estimate: about 1,800 input tokens (instructions, rules, a short example, about 15 facts) and about 900 output tokens (about 350 of text plus gpt-oss reasoning) at $0.155 / $0.618 per million tokens:

| | Cost |
|---|---|
| One product, first reply accepted | **≈ $0.0008** |
| One product, reply rejected once | ≈ $0.0017 |
| A 5-product batch (POC limit) | ≈ $0.004–0.009 |
| Reused reply (retry, resume, same facts) | $0 |
| Products the $10 monthly cap covers | ≈ 6,000–12,000 |

Each user is capped at 2 lifetime submissions × 5 products, plus up to 10 versions per product when facts change. So enrichment is a negligible share of the cap. I'll measure real cost in the live test and record it.

## 12. Tests

**Locally:** only M7's new tests plus the fast tests that need no database. DB tests run against Neon `test`. CI runs the full suite. Bedrock is always stubbed with the `fake` backend, with no live calls in CI. An opt-in `tests/test_enrichment_live.py` makes one real call, run only with your OK.

| Area | Cases |
|---|---|
| Grounding (fast, no DB) | Every check in §5.3, passing and failing. "1.5 L" vs fact 1500 ml passes, and "2 L" fails. "Dishwasher safe" without a supporting fact fails, and with the supplier description saying so passes. A colour with colour missing fails. An invented fact ID fails. A cited but unused fact fails. Superlatives fail. Prompt-injection text inside the description fact is treated as data. |
| Facts (fast) | Only `verified`, `reviewer_set` and a sure or person-chosen category are used. Excluded attributes are never sent. The fingerprint is stable and changes when a value changes. The too-little-to-write-about rule skips with no call. |
| **Fabrication** | Products missing capacity, material and colour. A stub reply that adds each is rejected, the retry feedback names the claim, and nothing is stored. A missing value never appears in stored text. |
| Malformed / truncated | Cut-off JSON, no JSON, wrong types, too many bullets, an overlong title: retried once, then `failed` with the reason. The run still succeeds. |
| Reuse on retry | A job killed after the call but before commit (lease lost) reruns with **0 new calls** and the same `model_run_id`. Resume after a pause doesn't call again for products already done. |
| **Cap mid-run** | A 5-product run with the cap set so product 3 hits it. Products 1–2 have text, the run is `paused`, the rest are `blocked_quota` with no attempts used, and the claim query ignores them. EC2 idle shutdown isn't blocked. Resume with the cap raised finishes 3–5. Resume with the cap still reached returns 409. A paused run doesn't block a new `ingest` run. |
| Out of date | A review decision changes a cited attribute, the text shows out of date, and Regenerate creates version 2 while version 1 stays stored and stops being current. |
| Queue | `block()` is fenced (a stale writer can't block). `resume_run()` is atomic with the active-run index. |
| API | Cross-user, unauthenticated and waitlisted on all four endpoints. The conditions behind each 409 and 422. |
| Migration | `0010` upgrades on top of `0009` data. Old rows keep working. The `paused` CHECK accepts old statuses. |

## 13. Opening sign-ups after M7

M7 completes the paid AI path that the cap protects. Before opening sign-ups, these remain from earlier modules, for your decision then, not part of M7:
- Your own Google OAuth client and a published consent screen (PROJECT_CONTEXT §12, pending).
- The M1 admin-role issue (admin-only test runs are unreachable).
- M4 has never had a live acceptance run.

## 14. Edge cases

- A product deleted while its job is blocked: the job finds no product and succeeds with nothing to do.
- A batch deleted while paused: everything cascades away.
- Attributes change between `enrich_plan` and `enrich_product`: the job builds facts when it runs, so it uses the latest values.
- Every attribute is `conflict`: the product is skipped ("not enough verified information") with no charge.
- The model returns facts in a different language or script: check 3 fails and the reply is retried.
- A person sets a value containing a claim word (for example `reviewer_set` material "food-grade steel"): the fact supports the claim, as intended.
- `processing_enabled` false: the start endpoint refuses, as other runs do.

## 15. Decisions for approval

Each will be recorded as part of **D37** once approved.

| # | Decision | Recommendation |
|---|---|---|
| 1 | When enrichment runs | **On demand, as a separate `enrich` pipeline** started from the batch page, using no submission and not changing batch status. Not an automatic final stage of `ingest`. |
| 2 | Which attribute states feed text | **`verified` and `reviewer_set` only**, plus the category when person-chosen or `assigned`. |
| 3 | Regeneration | **Only when text is out of date or failed.** No reroll of identical facts, which would return the same stored text anyway. |
| 4 | Excluded attributes | **Price, currency, SKU, GTIN and supplier category never sent.** Model number allowed in the title only. |
| 5 | How strict the grounding is | **Claims strict** (numbers, units, colours, materials, safety and certification words, banned superlatives), with ordinary descriptive wording allowed. Not "every content word must come from a fact". |
| 6 | Cap reached mid-run | **Pause:** jobs `blocked_quota`, run `paused` (doesn't hold the active-run slot), **manual Resume** once spend is under the cap. No automatic resume. |
| 7 | `generated_content` shape | **One row per product version** with title, description, bullets and frozen per-claim citations, replacing the planned one-row-per-field design. |
| 8 | Version cap | **10 versions per product** (config). |
| 9 | Unusable after retry | **Mark the product `failed` with reasons and keep the run going.** No review task until M9 adds editing. |
| 10 | Length limits | **Title ≤ 150 characters, 3–5 bullets ≤ 200 characters each, description 2–5 sentences ≤ 1,000 characters**, all in config, until M8's marketplace rules. |
| 11 | Live check | **One opt-in live call** on a real product (about $0.001) before shipping, with your OK at the time. |

## 16. Process from here

1. You approve or amend the decisions above.
2. I implement on a branch and run local checks: new tests plus fast tests, with DB tests on Neon `test`. **I'll ask before applying `0010` to `test`.**
3. I ask before shipping. You commit and push, CI runs and deploys, and CI migrates `production`.
4. Your live test: generate content for a batch, review an attribute, then see the text go out of date and regenerate it.
5. Log check, then I mark M7 Completed and record D37, the glossary terms (fact, claim, grounding check, out-of-date content, paused run) and the docs updates.
