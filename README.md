# CashCo V2 — Partner Billing Control Tower

CashCo V2 is a controlled billing and settlement workspace for restaurant partners. The application currently remains in its safe pre-Phase 2 state: automation is `OFF`, it is waiting for Admin authorization, and no source ingestion, Drive write, document, or email workflow is enabled.

## Architecture and source boundaries

The application loads typed configuration centrally from environment variables. Google service-account JSON is parsed directly from `GOOGLE_SERVICE_ACCOUNT_JSON` in memory; it is never expected as a repository file.

The confirmed Drive sources are:

- **Admin Earnings:** read-only operational order/earnings source. It does not define a settlement period by filename.
- **Finance Tracking:** read-only annual partner/payment tracking source. Its future period/payment information will determine the Finance population.
- **RST List:** read-only restaurant enrichment source. It does not define settlement eligibility.
- **CashCo workspace:** Config, Processed, Partners, Documents, and Audit will require read/write access in later phases.

The earlier `PAYMENT_SCOPE` concept—especially the assumption of one file per P1/P2 period—is deprecated. New configuration and documentation use `FINANCE_TRACKING` or `FINANCE_SCOPE`. Existing legacy modules are not migrated here because this change intentionally does not implement Phase 2 business logic.

## Secure Google setup

1. Create the Google service account `cashco-app@cashco-app.iam.gserviceaccount.com` and create/download its JSON key.
2. Never add that JSON file to this repository, copy it into the Codespace, or commit it.
3. In GitHub, create a Codespaces secret named `GOOGLE_SERVICE_ACCOUNT_JSON`.
4. Store the compact, complete JSON content as the secret value.
5. Share the Drive objects with the service account using these permissions:

   - Admin Earnings: Viewer
   - Finance Tracking: Viewer
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
CASHCO_RST_LIST_FILE_ID=
CASHCO_FINANCE_TRACKING_FILE_ID=
CASHCO_FINANCE_TRACKING_FOLDER_ID=
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

## Phase boundary

This repository configuration prepares the secure environment for Phase 2. It does not connect to Drive, discover or parse sources, generate Google Sheets or documents, send email, or enable automation.
