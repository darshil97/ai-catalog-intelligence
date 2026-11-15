# M9 Human review — proposal

**Status:** awaiting the owner's approval. Nothing is built yet.
**Written:** 2026-11-15, against `main` at `34054e6` (M8 and the UI redesign shipped; `production` at migration `0011`).
**Builds on:**
- M5B and M6: review items for photos, conflicting values and categories, and the append-only `review_events`.
- M7: generated copy and its out-of-date reasons.
- M8: checks, errors and set-aside warnings.
- The redesigned batch and product pages (D39).

**Leads to:** M10 (export only approved products).

---

## 1. Objective

Give a person everything needed to take a batch from "processed" to "approved":
- **Fix values directly,** including the two cases testing found impossible: a missing brand, and two products sharing a barcode.
- **Edit copy by hand.**
- **Settle duplicates.**
- **Approve or reject products,** one at a time or in bulk.

Every change is recorded with its before and after, and shown as a product's history. Only approved products reach export.

## 2. Scope (IMPLEMENTATION_PLAN M9, plus the additions agreed in testing)

**In scope**
- **Editing values:** set or clear any attribute of a product, not only through a review item. Each check gets a fix link to the value it's about.
- **Editing copy:** change the title, description or bullets by hand, kept apart from generated copy.
- **Duplicates:** mark a flagged pair as the same product, which rejects one, or as different products.
- **Approval:**
  - approve and reject, and undo either;
  - an approved product returns to draft when it changes;
  - bulk "approve all ready" and bulk reject.
- **A review page across batches:** every open review item in one place, with a count in the navigation.
- **History:** a timeline per product, read from `review_events`.
- **Concurrent edits:** a change made from a stale page is refused, not lost.

**Out of scope**
- Export files (M10).
- Teams, roles or assigning items to people. There's one person per account, as today.
- Editing a product's code, or merging products.
- Undoing an arbitrary past change. History is read-only; you make a new change instead.

## 3. Acceptance criteria (from the plan)

1. Every change is recorded with its before and after values, who made it and when: value edits, copy edits, duplicate decisions, approvals, rejections, and undoing any of these.
2. **Only approved products can be exported:** M10 reads one function, and that function returns only products whose approval is still valid.
3. The missing-brand and duplicate-barcode errors from testing can be fixed in the app.
4. Two tabs editing the same product can't overwrite each other silently: the later one gets "changed since you loaded it".
5. Cross-user, signed-out and waitlisted access is refused on every new endpoint.

## 4. How it works

### 4.1 Editing values (decision 1)

- **Edit:** each row of the product's Catalog values table gets an **Edit** button. You can set a value or clear it, with an optional note.
- **Unit check:** a value is read with the same unit rules as M6. A quantity must have a unit ("1.5 L", "350 ml"), so "1.5" alone is refused with a message, rather than stored as an unclear value. A price without a currency is taken as rupees, as in M6.
- **What's stored:** a set value becomes `reviewer_set`, exactly like typing your own value in a conflict today. A cleared value becomes `missing` decided by you, so it stays empty and processing never fills it again. Normalization already leaves any row a person decided alone.
- **Fix links:** each check names the value it's about, so "Brand is required" and "same barcode" link straight to that value's Edit. Saving clears the check on the next read and marks the copy out of date, naming your change (M7's reasons).
- **Review items:** editing a value that has an open conflict resolves that item too, recorded as one decision.

### 4.2 Editing copy (decision 2)

- **Edit:** the product's Generated content section gets **Edit**, with fields for the title, description and bullets.
- **Stored:** a saved edit becomes a new version with status `edited`, `created_by` you, and no citations.
- **Your edits stand:** automatic writing never replaces copy you edited. If its values change later, it shows "Out of date", and **Regenerate** asks before discarding your edit.
- **Checked as warnings:** the M7 grounding check also runs on your text, and anything it would have refused shows as a warning, such as "Mentions 'dishwasher safe', which no value says". It's your text, so it can be set aside, never silently changed.

### 4.3 Duplicates (decision 3)

For a pair the checks flag (same barcode, model number, brand and name, or photo):
- **"Same product, keep this one":** the other product is **rejected**, with the reason "duplicate of AK-1001". It drops out of checks, approval and export, and the pair's warnings and errors clear.
- **"Different products":** for a name, model-number or photo match, this sets the warning aside with that reason (M8's mechanism). A shared barcode is an error that can't be set aside, so the fix is to correct one barcode through Edit, as in 4.1.

### 4.4 Approval (decision 4)

- **States:** a product is `draft`, `approved` or `rejected`. The schema's unused `in_review` state stays unused.
- **What approval requires:**
  - no errors under the batch's marketplace profile;
  - copy that's written and current, whether generated or edited by you;
  - no open review items.

  Approve is disabled with the reasons listed. Warnings don't block.
- **An approval stays valid only while the product is unchanged:** approval stores a fingerprint of the product's values, copy, photos, category and profile. If anything changes afterwards, the product shows **"Changed since approval"** and counts as draft again until re-approved. This is worked out on read (as in D37 and D38), so nothing can forget to clear it. Export uses only products whose approval is still valid.
- **Reject:** takes a reason. Rejected products stay visible, greyed out, and can be restored to draft.
- **Bulk:** the batch page offers **"Approve all ready (N)"**, which skips anything that doesn't qualify and says why. Products can also be selected and rejected together.
- **Next step:** the next-step card gains a final step, "N products ready to approve", before "All approved; export arrives in M10".

### 4.5 One review page (decision 5)

- **Review page:** a **Review** item in the navigation shows a count of open items across all your batches. It opens one list grouped by batch and kind (photos, values, categories), with the same decision controls as the batch's Review tab.
- **Copy after decisions:** the last decision for a batch still queues its copy (D37 amended).

### 4.6 History (decision 6)

- **Product page:** a History section lists every recorded change to the product, newest first. Each entry shows what changed, from what to what, by whom, and when, for example *"You set Brand to 'Vardhan' (was missing) · 18:05"*.
- **Sources:** review decisions, value and copy edits, set-asides, duplicate decisions, approvals and rejections, all from `review_events`. Processing steps appear as "Processed", "Copy written (v2)".
- **The trail can't be edited:** the table's trigger already refuses updates, and a test proves it.

### 4.7 Concurrent edits (decision 7)

- **A revision number:** each product gets a `revision` that every change increments. Write requests carry the revision the page loaded.
- **A stale write:** if the revision moved, the API answers **409 `changed_elsewhere`**, and the page reloads that product with "This product changed since you opened it; check and try again".
- **Review items:** these already lock their row and answer 409 "already decided".

## 5. Schema — migration `0012`, add-only

**I'll ask before applying it to `test` or `dev`. CI applies it to `production` on ship.**

| Change | Notes |
|---|---|
| `products.revision` INTEGER NOT NULL DEFAULT 0 | Optimistic lock (4.7) |
| `products.approved_at`, `approved_by`, `approval_fingerprint` | Set on approval; the fingerprint decides whether it's still valid |
| `products.rejected_at`, `rejected_by`, `rejection_reason` | Set on rejection |
| `products.duplicate_of` UUID FK nullable | The product kept, when rejected as a duplicate |
| `generated_content` | No change: the `edited` status and `created_by` already exist (M7) |
| `review_events` | No change: new entity types `product_value`, `generated_content`, `product_status`, `duplicate` |

`products.status` keeps its existing check (`draft`, `in_review`, `approved`, `rejected`).

## 6. API

| Endpoint | Does |
|---|---|
| `PUT /products/{id}/values/{code}` | Body `{value \| null, note?, revision}`. Sets or clears a value. 422 for an unreadable quantity. 409 when stale. |
| `PUT /products/{id}/copy` | Body `{title, description, bullets, revision}`. Saves a hand-edited version. |
| `POST /products/{id}/approve` | Body `{revision}`. 422 `not_ready`, with the reasons. 409 when stale. |
| `POST /products/{id}/reject` | Body `{reason, revision}` |
| `POST /products/{id}/restore` | Back to draft, from approved or rejected |
| `POST /products/{id}/duplicate` | Body `{other_id, keep: "this" \| "other" \| "different", revision}` |
| `POST /batches/{id}/approve` | Body `{product_ids?}`. Approves every ready product (or those listed) and returns what was approved and why others weren't. |
| `POST /batches/{id}/reject` | Body `{product_ids, reason}` |
| `GET /review` | Open review items across all batches, grouped |
| `GET /products/{id}/history` | The product's timeline |
| `GET /products/{id}`, `GET /batches/{id}/products` | Gain `status`, `approval` (`approved`, `changed_since_approval`, or `rejected`, with reasons) and `revision` |

M10 calls `approved_products(batch_id)`, which returns only products with a valid approval.

## 7. Frontend

- **Batch page:**
  - a Products tab with status badges and checkboxes;
  - "Approve all ready (N)" and "Reject selected";
  - rejected products greyed out;
  - the next-step card's final approval step.
- **Product page:**
  - Edit on each value;
  - Edit on the copy;
  - Approve, Reject and Restore in the header, with the approval requirements listed when they aren't met;
  - duplicate actions on duplicate checks;
  - fix links on every check;
  - a History section.
- **Review page:** a new `/review` with a nav count.

## 8. Cost

- **AI:** nothing for review itself. Rewriting copy after edits costs as M7 does, about ₹0.08 per product.
- **Database:** about one extra query on the product and batch reads, for approval state and revision.

## 9. Tests

**Locally:** M9's new tests plus the fast tests. DB tests run on Neon `test`, and CI runs the full suite.

| Area | Cases |
|---|---|
| Value edits | Set a missing brand, and the check clears. Clear a wrong barcode, and the duplicate error clears. A quantity without a unit is refused. Editing a value with an open conflict also resolves that item. A cleared value stays cleared after photo values are read again. The edit is in history, with before and after. |
| Copy edits | Saving makes version N+1 with status `edited`. Automatic writing skips it. Regenerate asks before discarding it. A claim no value supports shows as a warning. |
| Duplicates | "Same, keep this one" rejects the other, and the pair's issues clear. "Different" on a name match sets the warning aside. A duplicate barcode can't be marked "different". |
| Approval | Refused with errors, missing copy or open reviews, each with its reason. Approved, then a value change shows "Changed since approval" and the product is no longer exportable. Restore works. Bulk approve approves only ready products and reports the others. |
| Concurrency | Two edits with the same revision: the second gets 409 and nothing is lost. Two simultaneous approvals behave the same. |
| Audit integrity | Every write path adds exactly one event with before and after. An attempt to update `review_events` is refused by the trigger. |
| API | Cross-user, signed-out and waitlisted on every new endpoint. |
| Export contract | `approved_products` returns only products with a valid approval. |
| Migration | `0012` on top of `0011`; existing products start as `draft` with revision 0. |

## 10. Decisions for approval

Each will be recorded as part of **D40** once approved.

| # | Decision | Recommendation |
|---|---|---|
| 1 | Editing values | **Set or clear any value, read with M6's unit rules, stored as a person's decision** that processing never overwrites. Editing a value with an open conflict resolves the conflict too. |
| 2 | Editing copy | **Allowed, kept as an `edited` version,** never replaced automatically. The grounding check shows warnings on hand-written text but doesn't block it. |
| 3 | Duplicates | **"Same product" rejects one with a reason; "different" sets the warning aside.** A shared barcode must be fixed by editing. |
| 4 | What approval requires | **No errors, current copy and no open review items.** An approval is valid only while the product's fingerprint is unchanged, worked out on read. |
| 5 | Review across batches | **A `/review` page with a count in the navigation**, alongside each batch's Review tab |
| 6 | History | **A per-product timeline from `review_events`**, read-only |
| 7 | Concurrent edits | **A product `revision` number with 409 `changed_elsewhere`** on stale writes |
| 8 | Bulk actions | **"Approve all ready" and "reject selected" only.** No bulk accepting of AI suggestions, so every review decision stays a deliberate one. |

## 11. Process from here

1. You approve or amend the decisions above.
2. I implement on a branch and run local checks. **I'll ask before applying `0012` to `test`.**
3. I ask before shipping, and CI migrates `production` on merge.
4. Your live test with the Vardhan batch:
   - fix the mug's brand and one barcode;
   - mark the bottles as different products;
   - edit one product's copy;
   - approve all ready products, then change one and see it leave "approved".
5. Log check, then I mark M9 Completed with D40 and the glossary terms.
