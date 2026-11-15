# Glossary

Plain-English meanings of terms used in this project, each with an example. Add new terms as they appear.

## Architecture

| Term | Meaning | Example here |
|---|---|---|
| **Frontend** | The part people see and use in their browser. | The Next.js app on Vercel. |
| **Backend** | The code running on servers, out of the user's sight. | Everything in `backend/`: the API and the worker. |
| **API** | The service the frontend talks to. It answers requests and hands off slow work. | "Create this batch" goes to FastAPI on Render. |
| **Worker** | A separate program that does slow, heavy work in the background. It takes jobs from a queue instead of answering users directly. | `worker.py` on EC2 reads PDFs and creates image embeddings. |
| **Entry point** | The file you run to start a program. | `server.py` starts the API, and `worker.py` starts the worker. |
| **Process** | A running program. | The API and worker are separate processes, so a worker crash doesn't take down the API. |
| **Shared module** | Code used by both the API and worker. | `app/db` and `app/storage`. |
| **Registry** | A lookup list of interchangeable implementations, chosen by name. Adding one doesn't change existing code. | The exporter registry holds `csv`, `xlsx` and `shopify`. |
| **Plugin** | One implementation inside a registry. | The `shopify` exporter. |
| **`noop`** | "No operation": an implementation that accepts a call and deliberately does nothing. It lets the same code run everywhere. | On a laptop, `PowerController.power_off()` does nothing, so your computer doesn't shut down. |

## Processing

| Term | Meaning | Example here |
|---|---|---|
| **Pipeline** | The fixed sequence of processing steps a batch goes through. | Ingest, extract, images, associate, normalize, enrich, validate. |
| **Stage** | One step of the pipeline. | `normalize`. |
| **Pipeline run** | One execution of the pipeline for a batch. | Pressing "process" on a batch creates one run. |
| **Submission** | The user action that starts a pipeline run, and what the POC limits count. | Each user gets an example limit of 2. |
| **Job** | One unit of background work stored in the database, waiting for the worker. | "Run OCR on these images." |
| **Chunk** | A small slice of a larger task, handled as one job, so that a failure repeats only that slice. | A group of 25 images. |
| **Handler** | The code that knows how to perform one type of job. | The handler for `extract_structured`. |
| **Queue** | The list of jobs waiting to run. | The `jobs` table in Neon. |
| **Claim** | The worker safely taking a job for itself, so nothing else runs it. | The `SKIP LOCKED` query. |
| **`SKIP LOCKED`** | A PostgreSQL feature that skips rows another transaction is working on, rather than waiting. | Two claims at once get different jobs. |
| **Lease** | A time-limited hold on a job. If the worker dies, the hold expires and the job becomes available again. | 120 seconds, renewed while working. |
| **Heartbeat** | A regular "I'm still alive" signal. | Every 30 seconds, the worker renews its lease. |
| **Reaper** | A routine that finds jobs whose lease expired, and puts them back in the queue. | It runs on startup and every 60 seconds. |
| **Retry** | Trying again after a temporary failure. | After a Bedrock throttling error. |
| **Backoff** | Waiting progressively longer between retries. | 10s, then 30s, then 60s. |
| **Transient vs permanent error** | Transient may succeed if retried. Permanent won't. | A network timeout vs a corrupt file. |
| **Idempotent** | Running something twice gives the same result as running it once. | A retried chunk updates its rows instead of duplicating them. |
| **Stale writer / fencing** | A worker that lost its lease but doesn't know yet. A token check stops it saving outdated results. | The result transaction checks `lease_token`. |
| **Drain** | Letting current work finish while accepting no new work. | Before a worker deploy. |
| **Poison file** | A file that repeatedly crashes processing. | Attempts count at claim, so it eventually stops retrying. |
| **Contract version** | A number stating the job format a worker can understand. | A job newer than the worker waits rather than failing. |
| **Idle timeout** | How long the worker waits with no work before shutting down. | 5 minutes. |

## Catalog

| Term | Meaning | Example here |
|---|---|---|
| **Supplier** | A company providing product data. | A kitchenware brand. |
| **Batch** | One set of files from a supplier, processed together. | "October price list." |
| **SKU** | Stock Keeping Unit: the identifier for one sellable product. | `KT-1042-BLK`. |
| **Variant** | A version of a product, such as a different color or size. | Flagged by the system, never merged automatically. |
| **Canonical schema** | The one standard structure all products are converted into. | Whatever a supplier calls it, the attribute is `net_weight`. |
| **Attribute** | One property of a product. | Material, capacity, net weight. |
| **Observation** | A value exactly as found in a source, with its location. Never changed. | "1.5 L" in sheet 1, row 7, column D. |
| **Provenance** | The record of where a value came from. | Which file, page and extractor produced it. |
| **Extraction** | Pulling raw values out of files. | Reading a spreadsheet cell. |
| **Column mapping** | Deciding which attribute each column of a sheet holds. | "Gew. netto (g)" is `net_weight`. |
| **Known name** | A column name listed in config for an attribute. Matched first, with no LLM. | "Item No." means SKU. |
| **Saved mapping** | A supplier's column mapping, remembered by its header layout and reused. | The same template next month needs no LLM call. |
| **Header fingerprint** | A hash identifying a header layout, used to find a saved mapping. | Changes if a column is added. |
| **Product key** | The cleaned identifier that makes rows one product. | "ab-77" and "AB-77 " are both `AB-77`. |
| **Differs** | An attribute whose sources give different values. All are kept, and none is chosen. | The sheet says 1.5 L, the PDF 1.2 L. |
| **Match key** | The product key without punctuation, for looser matching later. | `AB77`. |
| **Source record** | One spreadsheet row as the file gave it, with every cell. | Row 14, including unmapped columns. |
| **GTIN** | The number under a barcode, also called EAN or UPC. Its last digit checks the rest. | `4006381333931`. |
| **Normalization** | Converting values into a standard form using fixed rules. | "1.5 L" becomes 1500 ml. |
| **Enrichment** | Generating titles, descriptions and bullets from verified values. | Written by an LLM, citing the attributes used. |
| **Validation** | Checking records against rules. | Flagging a missing brand. |
| **Conflict** | Two sources give different values for one attribute. Both are kept, and a person decides. | The spreadsheet says 1.5 L, and the PDF says 1.2 L. |
| **Review task** | An item waiting for a person's decision. | An uncertain image match. |
| **Fabrication** | An AI stating a specification that no source supports. The product must never do this. | Inventing "dishwasher safe". |

## Image and AI

| Term | Meaning | Example here |
|---|---|---|
| **pHash** | Perceptual hash: a short fingerprint of how an image looks. Similar images give similar fingerprints. | Finds resized copies. |
| **Near-duplicate** | Two images of the same picture: resized, re-saved, cropped a little or brightened. Grouped, never removed. | The same photo uploaded at two sizes. |
| **Representative** | The image a near-duplicate group is shown by: its largest. | The full-size copy, not the thumbnail-sized one. |
| **Hue histogram** | How much of each colour an image holds, ignoring brightness. Keeps colour variants apart. | A red mug and a blue mug differ; a brighter copy doesn't. |
| **Thumbnail** | A small copy of an image for showing in the app. | 320 px on its longest side. |
| **Embedding** | A list of numbers representing meaning, so similar things have similar numbers. | Each image gets 512 numbers. |
| **CLIP / OpenCLIP** | An AI model that creates embeddings for images and text. | `ViT-B-32` on CPU. |
| **Inference** | Running an AI model to get a result. | Creating an embedding. |
| **pgvector** | A PostgreSQL extension for storing and comparing embeddings. | The `image_embeddings` table. |
| **Cosine similarity** | A score for how close two embeddings are. | Finding visually similar images. |
| **HNSW** | An index that makes vector search faster but approximate, and takes storage. | Not used initially. |
| **OCR** | Optical Character Recognition: reading text from images. | Reading a model number on a box. |
| **Candidate** | A product an image might show, with its score and evidence. | `brand.jpg` might be AK-1003 or AK-1004. |
| **Auto-accept** | Linking an image to a product without a person, allowed only when the image's own code names exactly one product. | A label photo showing `B094YFFSMY`. |
| **Audit trail** | The record of every review decision, with before and after, who and when. It can't be changed. | `review_events`. |
| **Signal** | One independent piece of evidence for an image↔SKU match. | SKU in the filename. |
| **LLM** | Large Language Model: an AI that reads and writes text. | Writing product descriptions. |
| **Amazon Bedrock** | AWS's service for calling AI models. | Called by the worker. |
| **LangChain** | A Python library giving one interface for calling different AI models. | All LLM calls go through it. |
| **LangGraph** | A library for LLM workflows with branching and loops, built around LangChain. | Used only if a module truly needs it. |
| **Inference profile** | A Bedrock ID that routes requests across a defined set of regions. | Global or Australia routing. |
| **Token (AI)** | A small piece of text used to measure and bill LLM use. | Price is per million tokens. Different from a JWT. |
| **Schema validation** | Checking data matches an expected structure. | Rejecting a malformed LLM response. |
| **Model run** | The record of one LLM call: model, tokens, cost and result. | One row per column-mapping call. |
| **Quote check** | Keeping an LLM-reported value only if its quoted text is on the page and contains the value. | "Capacity: 1.2 L" must be on page 2. |
| **Scanned page** | A PDF page that is a picture of text, with little or no text of its own. Read with OCR. | A spec sheet run through a scanner. |
| **Spend cap** | The most all users' LLM calls may cost in a month. Once reached, LLM steps are skipped. | $10. |
| **Prompt injection** | Text in a document trying to give instructions to the AI. | A PDF saying "ignore your rules". Supplier text is treated as data. |

## Infrastructure

| Term | Meaning | Example here |
|---|---|---|
| **EC2** | AWS virtual machines. | The worker's `t3a.medium`. |
| **EBS** | The disk attached to an EC2 machine. It bills even when the machine is stopped. | 20 GB gp3. |
| **vCPU** | A virtual processor. | `t3a.medium` has 2. |
| **CPU credit** | A prepaid balance for running above a baseline speed on `t3a` machines. 1 credit is 1 vCPU at full speed for 1 minute. | Earns 24 per hour, and spends 120 at full load. |
| **Unlimited mode** | When credits run out, stay fast and pay a small extra charge. | Chosen in D4. |
| **Standard mode** | When credits run out, slow down to baseline. | 20% speed, not chosen. |
| **S3** | AWS file storage. | Stores uploads and exports. |
| **Presigned URL** | A temporary link that allows one specific upload or download without further login. | The browser uploads directly to S3. |
| **Gateway endpoint** | A free private route from a VPC to S3. | Worker file transfers. |
| **VPC / security group** | A private network, and its firewall rules. | No inbound rules on the worker. |
| **NAT Gateway** | A paid always-on service giving private machines internet access. | Deliberately avoided. |
| **Elastic IP** | A fixed public IP that bills while the machine is stopped. | Deliberately avoided. |
| **IAM** | AWS's system for who can do what. | Limits who can start EC2. |
| **IAM role** | Permissions a service takes on, with temporary credentials. | The worker's instance role. |
| **IAM user** | An identity with long-lived keys. | `catalog-api`, because Render isn't on AWS. |
| **Least privilege** | Granting only the permissions actually needed. | The API can start one instance, and nothing else on EC2. |
| **OIDC** | A way for GitHub Actions to get temporary AWS access without stored keys. | Worker deploys. |
| **SSM (Systems Manager)** | AWS's way to run commands on a machine with no open ports. | Deploy commands. |
| **Parameter Store** | Secure storage for settings and secrets, in AWS. | The worker's secrets. |
| **systemd** | The Linux service manager that starts and restarts programs. | Starts the worker at boot. |
| **Watchdog** | A separate check that acts when something stops responding. | Powers off EC2 if the worker is dead. |
| **Scale to zero** | A service shutting down when idle, and waking on demand. | Neon, after 5 idle minutes. |
| **Cold start** | The delay while a sleeping service wakes. | Render takes about a minute. |
| **Neon branch** | An isolated copy of a database, a bit like a Git branch. | `dev` and `production`. |
| **CU-hour** | Neon's compute unit: size multiplied by running time. | Free tier allows 100 per month. |
| **Connection pooler** | Shares a few database connections among many clients. | Used at runtime. |
| **Migration** | A versioned change to the database structure. | An Alembic file adding a table. |
| **Expand/contract** | Changing a schema in safe steps: add new, move over, remove old in a later release. | Keeps running code working. |
| **CloudFormation** | AWS's service for creating resources from a file. | Infrastructure as code (D8). |
| **Infrastructure as code** | Describing servers and services in files kept in Git. | `backend/infra/aws/`. |
| **CI/CD** | Automatically testing (CI) and deploying (CD) code. | `ci.yml`. |
| **Commit SHA** | The unique ID of a code version. | `/health` reports it. |
| **Deploy key** | A credential giving one machine read access to one repository. | EC2 pulling private code. |
| **Rollback** | Returning to the previous version after a failure. | `deploy.sh` restores the previous SHA. |

## Auth and limits

| Term | Meaning | Example here |
|---|---|---|
| **Authentication** | Proving who you are. | Google sign-in. |
| **Authorization** | What you're allowed to do. | Only seeing your own batches. |
| **OAuth** | The standard behind "Sign in with Google". | Requires a Google OAuth client. |
| **Neon Auth / Better Auth** | Neon's managed sign-in service, built on Better Auth. | Stores users in the `neon_auth` schema. |
| **JWT** | A signed token proving who the user is. | Sent with each API request, lasting 15 minutes. |
| **JWKS** | Public keys used to check JWT signatures. | Cached by the API, so no database query. |
| **`sub`** | The user ID inside a JWT. | Used as `user_profiles.id`. |
| **Key rotation** | Replacing the keys that sign tokens. Tokens name their key with a `kid`. | An unknown `kid` makes the API refetch the JWKS, at most once every 30 seconds. |
| **Profile** | This app's record of a signed-in user, created on first use. | A `user_profiles` row with a status of active, waitlisted or disabled. |
| **Allowlist** | Emails always let in, even while sign-ups are closed or the user cap is reached. | The owner's Gmail address. |
| **Waitlist** | Where signed-in users wait while sign-ups are closed or the user cap is reached. They can sign in, but can't use the app. | A stranger signing in before M7 sees the waitlist message. |
| **Override** | A per-user change to a limit. Null means unlimited. | An admin account exempt from submission limits. |
| **Restoration** | A ledger entry giving back usage after a platform failure. The original entry is never edited. | A submission lost to an EC2 failure is restored. |
| **Usage limit** | A cap on what one user can do. | 5 products per submission. |
| **Global cap** | A cap across all users, protecting the budget. | 30 EC2 hours per month. |
| **Usage ledger** | An append-only record of usage. | The `usage_ledger` table. |
| **Retention** | How long data is kept before deletion. | 30 days, as an example. |
| **`blocked_quota`** | A job paused because a global cap was reached. Not a failure. | LLM spend cap reached. |
| **POC** | Proof of Concept: a limited version showing that an idea works. | This project's current stage. |
