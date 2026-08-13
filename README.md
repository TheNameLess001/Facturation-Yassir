# CashCo V2 — Partner Billing Control Tower

CashCo V2 is a controlled billing and settlement workspace for restaurant partners. Its current source layer connects to Google Drive, ingests Admin Earnings, builds an in-memory Restaurant Registry from the official Invoice Scope and RST List, and evaluates P1/P2 financial eligibility. Automation remains `OFF`, awaiting Admin authorization; document, email, and payment workflows are not enabled.

## Architecture and source boundaries

The application loads typed configuration centrally from environment variables. Google service-account JSON is parsed directly from `GOOGLE_SERVICE_ACCOUNT_JSON` in memory; it is never expected as a repository file. Phase 3 requests the Drive scope required to publish processed artifacts, while source services expose only read operations and write operations are restricted to the configured CashCo workspace.

The confirmed Drive sources are:

- **Admin Earnings:** read-only transactional truth for orders. It does not define billing eligibility or a settlement period by filename.
- **Invoice Scope:** read-only official list of restaurants CashCo must invoice. Presence in the active scope is the only current billing-eligibility source.
- **RST List:** read-only restaurant identity and enrichment source. It does not define billing eligibility.
- **CashCo workspace:** Config, Processed, Partners, Documents, and Audit will require read/write access in later phases.

The annual Finance Tracking source, `FINANCE_TRACKING`, `FINANCE_SCOPE`, and the period-file `PAYMENT_SCOPE` model are deprecated and not used by CashCo V2. Legacy modules and enum values may remain solely for historical test/manifest compatibility; normal source discovery, readiness, billing eligibility, dashboard status, and authorization do not call or depend on them.

The active source chain is:

```text
Invoice Scope → RST Registry → Admin Earnings Orders → Settlement
→ Documents → Admin Authorization → Email
```

The source, registry, and settlement-eligibility stages exist today. Documents and later stages remain future boundaries.

## Secure Google setup

1. Create the Google service account `cashco-app@cashco-app.iam.gserviceaccount.com` and create/download its JSON key.
2. Never add that JSON file to this repository, copy it into the Codespace, or commit it.
3. In GitHub, create a Codespaces secret named `GOOGLE_SERVICE_ACCOUNT_JSON`.
4. Store the compact, complete JSON content as the secret value.
5. Share the Drive objects with the service account using these permissions:

   - Admin Earnings: Viewer
   - Invoice Scope: Viewer
   - RST List: Viewer
   - Config: Editor
   - Processed: Editor
   - Partners: Editor
   - Documents: Editor
   - Audit: Editor

6. Restart or rebuild the Codespace if the secret is not visible to an already-running container.

Never print or log credentials, private keys, service-account identity fields, or access tokens. Invalid JSON produces a controlled authentication/configuration error. Missing credentials produce `NOT_CONFIGURED` and do not prevent Streamlit from starting.

## Environment configuration

Copy `.env.example` to `.env` for non-secret local settings. Supply the real values through environment configuration:

```text
GOOGLE_SERVICE_ACCOUNT_JSON=
CASHCO_ADMIN_EARNINGS_FOLDER_ID=
CASHCO_INVOICE_SCOPE_FILE_ID=
CASHCO_RST_LIST_FILE_ID=
CASHCO_CONFIG_FOLDER_ID=
CASHCO_PROCESSED_FOLDER_ID=
CASHCO_PARTNERS_FOLDER_ID=
CASHCO_DOCUMENTS_FOLDER_ID=
CASHCO_AUDIT_FOLDER_ID=
```

Do not place real credentials in `.env.example`. Drive IDs are configuration, not business-logic constants, and are intentionally omitted from the main dashboard.

## Admin Earnings naming contract

Only `.csv` and `.xlsx` files following this convention will later be eligible:

```text
data week N_YYYY
data week 2_2026.csv
data week 31_2026.xlsx
```

`N` is a week number from 1 through 53 with no leading zero. It is not a month. The settlement period will later be derived from actual order dates.

Future duplicate handling must safely deduplicate identical normalized financial records for the same Order ID. Conflicting financial values for the same Order ID must be blocking, use `CONFLICTING_DUPLICATE`, and enter the review queue; no record may be selected silently.

## Start and validate

Python 3.11+ is supported; the devcontainer uses Python 3.12.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

Validation commands:

```bash
pytest -q
ruff check .
python -m compileall -q app.py pages src tests
git diff --check
```

## Invoice Scope and Restaurant Registry

The configured Invoice Scope workbook is profiled before use. The active worksheet is explicit (`CASHCO_INVOICE_SCOPE_WORKSHEET`, currently `CASH-CO`); historical worksheets are reported but never concatenated into the registry. Presence in the active list means `IN_SCOPE` unless a real, explicit eligibility column exists.

Restaurant mapping is deterministic, in this order:

1. exact normalized Restaurant ID;
2. exact unique normalized restaurant name;
3. manual review and correction in Invoice Scope.

Candidate ranking in Review Queue is advisory only. It may use name similarity, city, chain/brand tokens, and canonical Admin order availability to help a human locate the right RST record, but it never changes the mapping result. CashCo never fabricates Restaurant IDs, uses RIB as identity, silently fuzzy-matches names, or persists a second alias master. The authoritative correction is made manually in Invoice Scope, followed by **Refresh Google Sources**.

Chain membership is organizational only; each store retains its own Restaurant ID and orders. Unmatched, ambiguous, and conflicting scope restaurants remain blocking for identity readiness. Missing email, RIB, or legal data is retained as a separate email/document/payment readiness issue and does not invalidate an otherwise exact identity mapping. Missing or conflicting Invoice Scope commission blocks settlement eligibility without changing identity readiness.

The registry remains in application memory under the current My Drive existing-file constraint. It does not call `files.create` and does not publish a new Drive artifact.

## Phase 5 settlement eligibility

Settlement periods are derived exclusively from the actual canonical `order_date`: P1 is day 1 through 15 inclusive, and P2 is day 16 through the last calendar day. Filename week, upload date, and Drive modification time never determine a settlement period. The default selector is the latest complete period; open or future periods are labeled explicitly.

Phase 5 evaluates only canonical Admin Earnings orders whose Restaurant ID belongs to an identity-ready Invoice Scope restaurant. Identity-blocked and out-of-scope orders are counted separately and never enter financial calculations. Operational source status is immutable and separate from the system-derived financial decision. Unknown statuses and cancellation responsibilities remain `MANUAL_REVIEW`; no fuzzy cancellation rule is applied. Phase 6 can layer an append-only manual override over that unchanged system decision.

Invoice Scope commission is authoritative for settlement eligibility. RST commission is validation-only: a mismatch is visible but uses the valid Scope rate, while a missing or invalid Scope rate blocks the restaurant. Financial arithmetic uses `Decimal`, invalid values remain null/blocking, and count and money reconciliation prevent unexplained order loss.

### LegacyCalculationPolicy status

The full authoritative legacy CashCo implementation is **not present** in this repository, its reachable Git history, or available reference files. The initial repository prototype documents only `payable`, a `ROUND_HALF_UP` commission calculation, and `net payable`; it contains no authoritative HT, TVA, TTC, note de débours, or final document formulas. Phase 5 therefore profiles and classifies real orders and calculates safe settlement inputs, but deliberately leaves those unavailable legacy outputs null. They must not be inferred or redesigned without an authoritative legacy reference.

## Financial review and document safety

CashCo preserves four distinct order facts: the immutable source operational status, the system financial decision, the latest manual override, and the resulting final financial decision. Overrides are append-only records in the ignored local runtime database `data/local/financial_override_registry.sqlite3`. Every record captures the reviewer, reason code, timestamp, engine rule, previous decision, and superseded override. An override never changes Admin Earnings and immediately recomputes period counts, restaurant readiness, and reconciliation from the canonical dataset.

Invoice Scope commission is the billing authority when it is present and valid. RST commission remains a validation reference. A difference is exposed as `COMMISSION_MISMATCH`, while the valid Scope rate remains effective; a missing or invalid Scope rate is blocking and CashCo does not silently fall back to RST.

The legacy formula registry records evidence and confidence independently for every required output. No authoritative definitions have been found for the complete commission base, HT, TVA, TTC, note de débours, and final net payable chain. Consequently:

- production financial formulas remain **NOT VALIDATED**;
- previews are watermarked `DRAFT · NOT VALIDATED`;
- unknown financial outputs remain null rather than fabricated;
- document generation does not imply authorization;
- no Google Drive `files.create` operation is used.

The Documents page provides versioned local JSON previews for invoice, note de débours, and partner statement models. Automatic production publication requires authoritative formula evidence plus a destination that supports safe document creation (for example, an approved Shared Drive or authorized user OAuth workflow).

## CashCo operations dashboard

The shared UI system combines a deep navy navigation frame with CashCo violet actions, restrained coral alerts, soft green positive states, and a light operational canvas. Overview, Settlement, Review Queue, Documents, Partners, Data Sources, Email Center, Payments, Audit, and Admin Control share central design tokens and reusable status components. Dashboard values are real operational adapters for the selected period; formula-gated document, email, and payment stages are never presented as complete. Automation remains `OFF`, and authorization or delivery cannot occur while the production gates are unresolved.

## Final operational workflow and email safety

The complete operational chain is:

```text
Invoice Scope
→ Restaurant Registry
→ Admin Earnings
→ Settlement
→ Financial Review
→ Documents
→ Email Center
→ Admin Authorization
→ Send
→ Period Lock
```

Email automation is period-scoped and every new period defaults to `OFF`. `PREVIEW` creates only an in-memory/local package preview. Draft execution defaults disabled, and production delivery requires both `CASHCO_EMAIL_ALLOW_SEND=true` and `CASHCO_PRODUCTION_EMAIL_SEND_ENABLED=true`; both remain `false` in the example configuration. A Drive service-account credential is not treated as Gmail sending authority. Gmail needs a separately approved OAuth user or Workspace domain-delegation configuration.

Recipient selection is deterministic: `finance_email` takes precedence, then `email`; addresses are normalized and validated, and CC is empty unless a future explicit policy enables it. Packages bind the settlement snapshot, immutable document versions, recipient, subject, body, and attachment set using stable hashes. Admin SEND authorization requires the exact phrase `SEND {period_code}` and applies only to that immutable snapshot. Any material change invalidates it.

Before a provider call, backend policy checks identity, settlement, manual review, commission, financial data, legacy formulas, legal data, production documents, recipient, current Admin authorization, period state, idempotency, and the hard send flag. The send key prevents duplicate delivery across clicks, reruns, retries, and restarts. State becomes `SENDING` before the provider boundary, then `SENT` only after confirmed success; failures remain independently retryable while successful packages are not retried.

Period locking is Admin-only and requires exact typed confirmation. A locked period rejects financial overrides, document mutation, authorization changes, and sending. Controlled reopening requires a reason and `REOPEN {period_code}`, writes an audit event, and invalidates the prior authorization without deleting historical send records.

The current authoritative legacy formulas are still unavailable. Production documents, EMAIL_READY, authorization for SEND, and production delivery therefore remain blocked. Implementation approval is not GO-LIVE approval; production activation requires the separately documented financial, legal, Gmail, dry-run, safety-flag, and explicit human authorization checks.

## Source discovery boundary

The Data Sources page validates the real service-account connection, checks each active configured Drive location independently, inventories Admin Earnings sources, and maintains a metadata manifest. Overall readiness depends on Google authentication, Admin Earnings, Invoice Scope, RST List, and the required CashCo workspace folders. Finance Tracking has no effect. Settlement evaluation is read-only and in memory. Document preview is local and gated; no Google restaurant Sheet, Drive document publication, email, or payment workflow is enabled.

## Phase 3.1 conflict diagnostics

Conflict Diagnostics reads the existing Phase 3 Parquet outputs and performs an aggregate, read-only analysis. Lineage fields, source filenames/weeks, row numbers, and ingestion timestamps are excluded from the Phase 3 material comparison and cannot create a business conflict.

The diagnostic model proposes—but does not apply—these reconciliation candidates:

- formatting-only differences may be auto-resolvable after normalization is explicitly authorized;
- financial differences no greater than `0.005 MAD` may be precision artifacts, but remain excluded from canonical orders;
- lifecycle/status changes remain reviewable until source authority is established;
- any material financial difference above tolerance remains blocking;
- any Restaurant ID conflict remains blocking;
- cancellation-reason enrichment may be considered only when identity and financial fields are unchanged.

The current canonical output rules remain exact and unchanged. No Phase 3.1 result is written to Drive. If persistence is later required under the existing-file workaround, manually pre-create `admin_earnings_conflict_diagnostics.json` in Processed before adding an update-only publisher.
