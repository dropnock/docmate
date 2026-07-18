# DocMate

A multi-tenant document digitization management platform. DocMate manages the full lifecycle of physical document scanning projects — from cabinet/lot intake and agent indexing through supervisor QA, AQL-based acceptance sampling, and customer quality control — with a complete audit trail and analytics dashboard.

---

## Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick Start (Docker)](#quick-start-docker)
- [Local HTTPS Domains](#local-https-domains)
- [Service URLs](#service-urls)
- [Default Credentials](#default-credentials)
- [Local Development](#local-development)
- [Configuration](#configuration)
- [Seeding Initial Data](#seeding-initial-data)
- [Key Workflows](#key-workflows)
- [Tech Stack](#tech-stack)
- [Versioning & Releases](#versioning--releases)
- [Notes for Production](#notes-for-production)

---

## Overview

DocMate operates two isolated portals:

| Portal | Audience | Purpose |
|--------|----------|---------|
| **Digitizing Entity** | Internal staff (admins, supervisors, indexers, QA agents) | Manage organisations/projects, cabinet & lot intake, shift-based staff assignment, indexing workspace, QA review, analytics |
| **Customer** | Client organisation staff (supervisors, QC agents) | Review completed lots, run QC sampling, accept or reject individual records |

Core features:

- **Multi-tenancy** — every table is scoped by `tenant_id`; no cross-tenant data leakage
- **Self-service customer onboarding** — creating a customer Organisation auto-provisions a dedicated Keycloak realm (TOTP-enforced), an OIDC client scoped to that org's `*.docmate.local` subdomain, and an S3 bucket — no manual Keycloak/S3 setup required
- **Portal enforcement** — nginx injects an `X-Portal` header per virtual host (digitizing vs. customer); the backend cross-checks it against the JWT `portal` claim on every request
- **TLS-terminated local domains** — nginx routes `www.docmate.local` / `auth.docmate.local` / `<customer-realm>.docmate.local` over HTTPS with a self-signed wildcard cert, matching how Keycloak's `Secure` session cookie behaves in production
- **Batch workflow state machine** — `draft → submitted → indexing → qa_review → customer_qc → passed | rejected`
- **Cabinet & Lot tracking** — physical cabinets are assigned to a customer org and hold records; supervisors group indexed records into Lots for AQL sampling and release to customer QC
- **Shift-based staff assignment** — staff are assigned to a project on a specific shift with a role (`indexer` or `qa`); task assignment and self-service pickup are gated by that role, so an agent can only be handed work matching their current shift assignment
- **Dynamic indexing forms** — per-project document type schemas (JSON Schema) drive fully auto-generated forms (`@rjsf/antd`), including range expansion for Volume/Folio parcel fields
- **Pessimistic record locking** — one agent owns a record at a time; 409 if locked by another user
- **Record versioning** — immutable `RecordVersion` snapshots on initial indexing, on every QA submission (pass or rework), and after customer rejection
- **AQL acceptance sampling** — full ISO 2859-1 Inspection Level II table; auto-escalation (normal → tightened → reduced)
- **Full audit trail** — every state change, lock, assignment, submission, and version is logged to an append-only `audit_logs` table
- **Analytics** — staff productivity metrics, project KPI dashboards, burn-up "path to completion" chart
- **Stale task detection** — APScheduler job clears locks and flags overdue tasks every 15 minutes
- **S3-compatible file storage** — MinIO (dev) or AWS S3 (prod); presigned upload and view URLs
- **Unified design system** — single primary/slate palette, `lucide-react` icons, self-hosted Inter/JetBrains Mono fonts, responsive down to mobile (collapsible nav drawer, card-based tables)

---

## Architecture

```
                              Browser
                                 │
             https://*.docmate.local  (self-signed TLS, see below)
                                 │
┌────────────────────────────────▼──────────────────────────────────┐
│                              nginx                                  │
│  www.docmate.local / digitizing.docmate.local → Digitizing portal   │
│  auth.docmate.local                            → Keycloak           │
│  <customer-realm-slug>.docmate.local           → Customer portal    │
│  Injects X-Portal header per virtual host                           │
└────────────┬──────────────────────────────┬────────────────────────┘
             │                              │
    ┌────────▼──────────┐        ┌──────────▼──────────┐
    │  frontend-de       │        │  frontend-cust        │
    │  (React + Vite)    │        │  (React + Vite)       │
    │  Digitizing portal │        │  Customer portal       │
    └────────────────────┘        └────────────────────────┘
             │ /api/*                          │ /api/*
    ┌────────▼──────────────────────────────────▼───────────────────┐
    │                       backend (FastAPI)                        │
    │   JWT auth · tenant scope · portal claim check                 │
    │   Provisions a Keycloak realm + OIDC client + S3 bucket         │
    │   whenever a new customer Organisation is created               │
    └──────────┬──────────────────┬───────────────────┬─────────────┘
               │                  │                   │
        ┌──────▼──────┐   ┌───────▼──────┐   ┌────────▼────────┐
        │  PostgreSQL  │   │   MinIO/S3   │   │    Keycloak      │
        │  (primary DB)│   │ (file store) │   │  one realm per   │
        │              │   │              │   │  tenant org      │
        └─────────────┘   └──────────────┘   └──────────────────┘
```

### Backend layout

```
backend/app/
├── core/           # Config, async DB session, JWT/portal security
├── models/         # SQLAlchemy ORM models (all with tenant_id)
├── schemas/        # Pydantic request/response schemas
├── routers/        # FastAPI route handlers (never write business logic here)
├── services/       # All business logic
│   ├── audit_service.py             # write_event() — the only way to write audit logs
│   ├── batch_service.py             # state machine transitions
│   ├── lock_service.py              # acquire / release record locks
│   ├── version_service.py           # immutable RecordVersion snapshots
│   ├── task_service.py              # assign, start, complete (persists + versions edits), fail, bulk-reassign
│   ├── staff_assignment_service.py  # shift-role roster + task-eligibility gating
│   ├── cabinet_service.py           # cabinet CRUD, record intake/upload, batch creation from a cabinet
│   ├── lot_service.py                # lot creation, AQL sampling, release to customer QC
│   ├── aql_service.py               # ISO 2859-1 sampling + escalation
│   ├── analytics_service.py         # productivity + KPI + burnup queries
│   ├── keycloak_service.py          # per-tenant realm/client provisioning, Keycloak user management
│   └── s3_service.py                # bucket provisioning + presigned URLs
└── background/
    └── stale_checker.py      # APScheduler: stale task detection every 15 min
```

### Frontend layout

```
frontend/src/
├── portals/
│   ├── digitizing/pages/     # Organisations, Projects, Cabinets, Cabinet Assignment, Lots,
│   │                         # Staff Assignment, Shifts, Users, My Tasks, Stale Tasks,
│   │                         # Staff Productivity, Project KPIs, Record History
│   └── customer/pages/       # Lots (QC sampling), QC Workspace, Project KPIs, Record History
└── shared/
    ├── components/
    │   ├── AppHeader.tsx                # Top bar: brand, portal label, mobile nav toggle, sign out
    │   ├── AgentWorkspace.tsx           # Split-screen indexing/QA/QC workspace
    │   ├── SplitWorkspace.tsx           # react-split layout shell used by AgentWorkspace
    │   ├── SchemaForm.tsx               # rjsf-driven dynamic form
    │   ├── rjsf/CustomWidgets.tsx       # RangeArrayField, ParcelArrayField, CountryWidget, DateTextWidget
    │   ├── ImageViewer/                 # OpenSeadragon deep-zoom viewer
    │   ├── StatusDot.tsx                # Shared filled/outline status indicator
    │   ├── PageHeader.tsx / PageSkeleton.tsx  # Shared page chrome + loading states
    │   ├── ProductivityTable.tsx / PathToCompletion.tsx / RecordTimeline.tsx  # Analytics widgets
    │   └── WorkspaceErrorBoundary.tsx
    ├── routing/                         # ProjectScopedRoute (project picker via ?project= query param)
    ├── theme/                           # Ant Design ConfigProvider tokens (palette, radius, typography)
    ├── api/                             # Axios client, keycloak-js adapter, TanStack Query hooks
    └── types/                           # Shared TypeScript interfaces
```

---

## Prerequisites

- **Docker** ≥ 24 and **Docker Compose** ≥ 2.20
- **OpenSSL** (to generate the local dev TLS certificate)
- Ability to edit your machine's hosts file (`/etc/hosts` on macOS/Linux, `C:\Windows\System32\drivers\etc\hosts` on Windows)
- Ports `80`, `443`, `8080`, `8180`, `8000`, `5173`, `5174`, `5432`, `9000`, `9001` available

---

## Quick Start (Docker)

```bash
# 1. Clone
git clone https://github.com/dropnock/docmate.git
cd docmate

# 2. Generate the local dev TLS certificate (self-signed, covers *.docmate.local)
./nginx/certs/generate.sh

# 3. Add local domains to your hosts file — see "Local HTTPS Domains" below
#    (at minimum: www.docmate.local and auth.docmate.local)

# 4. (Optional) Set a strong secret key
echo "SECRET_KEY=$(openssl rand -hex 32)" > .env

# 5. Start everything
docker compose up --build

# First run takes ~2 minutes while Keycloak imports the realm.
# Wait until you see: "Application startup complete." in the backend logs.

# 6. Seed the initial tenant, organisations, and users
docker compose exec backend python seed.py
```

> **Subsequent starts** (no rebuild needed):
> ```bash
> docker compose up
> ```

> **After any backend code change:**
> ```bash
> docker compose up --build -d --force-recreate backend
> ```
> `docker compose up --build -d` alone rebuilds the image but will **not** recreate an already-running container — the old image keeps serving until you add `--force-recreate` (or `docker compose down` first).

> **After any frontend code change:**
> ```bash
> docker compose up --build -d --force-recreate frontend-de frontend-cust
> ```

---

## Local HTTPS Domains

Keycloak's session cookie is `SameSite=None; Secure`, which browsers only honor over HTTPS on a non-`localhost` hostname — so the digitizing and customer portals are served over `*.docmate.local` with a self-signed certificate rather than plain `http://localhost`.

Add the following to your hosts file, pointing at wherever Docker is reachable (`127.0.0.1` for a local Docker daemon):

```
127.0.0.1  www.docmate.local
127.0.0.1  digitizing.docmate.local
127.0.0.1  auth.docmate.local
127.0.0.1  acme-archive.docmate.local
```

- `acme-archive` is the realm slug of the sample customer organisation created by `seed.py`. Every customer Organisation you create afterwards (via the Organisations page) gets its own realm slug — add a hosts entry for `<slug>.docmate.local` the same way.
- The certificate is self-signed (`nginx/certs/generate.sh`), so your browser will show a security warning on first visit to each `*.docmate.local` host — accept/proceed past it (or import `nginx/certs/dev.crt` into your OS/browser trust store).
- `nginx/certs/dev.crt` and `dev.key` are git-ignored and generated locally; the compose stack won't start without them.

---

## Service URLs

| Service | URL | Notes |
|---------|-----|-------|
| **Digitizing Portal** | https://www.docmate.local | Main staff portal |
| **Digitizing Portal (no TLS setup)** | http://localhost | Same portal, works without hosts-file/cert setup — Keycloak login still requires the HTTPS domain |
| **Customer Portal** | https://\<customer-realm-slug\>.docmate.local | e.g. `https://acme-archive.docmate.local` for the seeded sample org |
| **Keycloak** | https://auth.docmate.local | Login pages, redirect target during OIDC flow |
| **Backend API** | http://localhost:8000 | FastAPI |
| **API Docs (Swagger)** | http://localhost:8000/docs | Interactive API explorer |
| **Keycloak Admin Console** | http://localhost:8180 | Identity provider admin |
| **MinIO Console** | http://localhost:9001 | S3-compatible object store UI |
| **Direct DE frontend** | http://localhost:5173 | Bypasses nginx/TLS entirely (fast iteration; portal header enforcement doesn't apply) |
| **Direct Customer frontend** | http://localhost:5174 | Bypasses nginx/TLS entirely (fast iteration; portal header enforcement doesn't apply) |

---

## Default Credentials

### Application users (Keycloak — `doc` realm)

> Created by `seed.py`. All passwords are `changeme123`. Users are prompted to set up TOTP on first login.

| Email | Role | Portal |
|-------|------|--------|
| `admin@doc.local` | Admin | Digitizing |
| `supervisor@doc.local` | DE Supervisor | Digitizing |
| `indexer@doc.local` | DE Staff (indexer shift role) | Digitizing |
| `qa@doc.local` | DE Staff (QA shift role) | Digitizing |
| `supervisor@acme.local` | Customer Supervisor | Customer (`acme-archive` realm) |
| `qc@acme.local` | Customer QC Agent | Customer (`acme-archive` realm) |

Additional customer organisations created via the Organisations page provision their own Keycloak realm and are seeded with users the same way, via the Users page.

### Infrastructure

| Service | Username | Password |
|---------|----------|----------|
| Keycloak Admin Console | `admin` | `admin` |
| MinIO Console | `minioadmin` | `minioadmin` |
| PostgreSQL | `docmate` | `docmate` |

---

## Local Development

### Backend (without Docker)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Start infrastructure only
docker compose up postgres minio keycloak

# Copy and edit env
cp .env.example .env

# Run migrations
alembic upgrade head

# Seed data
python seed.py

# Start dev server (hot reload)
uvicorn app.main:app --reload --port 8000
```

> Running the backend outside Docker talks to Keycloak over plain `http://localhost:8180`, so the TLS/hosts-file setup above isn't required for this path — only the Docker Compose stack serves portals over `*.docmate.local`.

### Frontend (without Docker)

```bash
cd frontend
npm install

# Digitizing portal (port 5173)
npx vite --port 5173

# Customer portal (port 5174) — in a second terminal
npx vite --port 5174

# Type check
npx tsc --noEmit

# Production build
npx vite build
```

### Alembic migrations

```bash
# Apply all pending migrations
alembic upgrade head

# Auto-generate a new migration (requires live DB)
alembic revision --autogenerate -m "describe the change"
```

### Running tests

```bash
cd backend
pytest
pytest tests/test_aql_service.py   # single file
```

---

## Configuration

Backend is configured via environment variables (see `backend/.env.example`):

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL async connection string | `postgresql+asyncpg://docmate:docmate@localhost:5432/docmate` |
| `SECRET_KEY` | Signing secret — **change in production** | `dev-secret-key-replace-in-production` |
| `AWS_ACCESS_KEY_ID` | S3 / MinIO access key | `minioadmin` |
| `AWS_SECRET_ACCESS_KEY` | S3 / MinIO secret key | `minioadmin` |
| `AWS_ENDPOINT_URL` | S3 endpoint (internal) | unset |
| `AWS_PUBLIC_ENDPOINT_URL` | S3 endpoint for presigned URLs (browser-accessible) | unset |
| `AWS_REGION` | S3 region | `us-east-1` |
| `S3_FORCE_PATH_STYLE` | Required for MinIO | `true` |
| `KEYCLOAK_INTERNAL_URL` | Keycloak URL for backend token validation / admin API | `http://localhost:8180` |
| `KEYCLOAK_EXTERNAL_URL` | Keycloak URL for browser redirects | `http://localhost:8180` |
| `KEYCLOAK_ADMIN_USER` | Keycloak admin username | `admin` |
| `KEYCLOAK_ADMIN_PASSWORD` | Keycloak admin password | `admin` |
| `CUSTOMER_PORTAL_BASE_URL` | Base URL (no subdomain) used to build each customer realm's OIDC redirect URIs, e.g. `https://docmate.local` | `http://localhost:8080` |

---

## Seeding Initial Data

The `seed.py` script creates:

- Tenant: **Digitizing Operations Centre** (slug: `doc`)
- Digitizing org: **DOC**
- Customer org: **Acme Archive Corp** (Keycloak realm: `acme-archive`, auto-provisioned with its own OIDC client and TOTP policy)
- Six users across both portals (see [Default Credentials](#default-credentials))

```bash
# Docker
docker compose exec backend python seed.py

# Local
cd backend && python seed.py
```

Beyond the initial seed, new customer organisations can be created directly from the **Organisations** page in the Digitizing portal — this provisions the Keycloak realm, OIDC client, and S3 bucket automatically, without touching `seed.py` or Keycloak by hand.

---

## Bulk Upload CLI

`scripts/bulk_upload.py` uploads every file in a local directory into a cabinet in one run — for large scan batches that shouldn't be dragged in one at a time through the UI.

It authenticates as a dedicated Keycloak service account (`docmate-cli`), not a human user — no interactive login, no password, independently revocable.

**One-time setup** (after `seed.py` has run and Keycloak is up):

```bash
# Docker
docker compose exec backend python provision_cli_user.py

# Local
cd backend && python provision_cli_user.py
```

This prints a client secret **once** — capture it (e.g. into a password manager, or a file for `--client-secret-file`). Re-running is safe and reprints the same secret; it never rotates.

**Usage:**

```bash
cd backend
python scripts/bulk_upload.py \
  --api-url https://www.docmate.local \
  --keycloak-url https://auth.docmate.local \
  --cabinet-id 42 \
  --directory /path/to/scans \
  --recursive \
  --client-secret-file /path/to/secret.txt
```

Run with `--dry-run` first to preview what would be uploaded without writing anything. See `--help` for the full flag list (concurrency, retries, CSV/JSON report output, custom CA bundle for the self-signed dev cert, etc.). Re-running against the same directory is safe — files are matched to records by filename, so already-uploaded files are updated in place rather than duplicated.

---

## Key Workflows

### 1. Create an organisation and project (Admin)

1. Log in as `admin@doc.local` → **Organisations** → **New Organisation** to onboard a customer (auto-provisions realm + S3 bucket)
2. **Projects** → **New Project**, linking it to a customer organisation and a document type's JSON Schema (defined per-batch, below)

**Example schema** (land title caveat):
```json
{
  "title": "Caveat Index Record",
  "type": "object",
  "properties": {
    "parcels": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "volume_number": { "type": "string" },
          "folio_number":  { "type": "string" }
        }
      }
    },
    "caveat_number":   { "type": "string" },
    "date_lodged":     { "type": "string", "format": "date" },
    "registered_owners": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name":    { "type": "string" },
          "address": { "type": "string" }
        }
      }
    }
  }
}
```

### 2. Intake documents via Cabinets (Admin/Supervisor)

1. **Cabinets** → **New Cabinet**, assigned to a project (and optionally a customer organisation)
2. Open the cabinet → **Upload Images** — drag and drop JPEG/PNG/TIFF/PDF files; each file creates one record in S3
3. **Cabinet Assignment** → allocate pending records from a cabinet to a batch and out to indexers

### 3. Assign staff to a shift (Supervisor)

1. **Shifts** → define a shift (time window + timezone) if one doesn't already exist
2. **Staff Assignment** → assign each staff member to a project + shift with a role: **indexer** or **QA**
3. Task assignment (manual or auto round-robin) only offers work to staff currently holding the matching shift role — a QA-shift agent won't be handed indexing tasks and vice versa

### 4. Index a record (Indexer)

1. **My Tasks** → click **Start Indexing** on an assigned task
2. The split-screen workspace opens: document image (left) + auto-generated form (right)
3. Fill in the form; press **Enter** to move to the next field, or use **Save Progress** to save without completing
4. Click **Submit & Complete** to submit, version the record, and release the lock — or **Skip** if the record can't be indexed at all (blank page, unreadable scan, wrong document), choosing **Withdrawn** or **Ineligible** as its new status
5. Either action automatically loads the next task in the same batch, until nothing is left assigned to you in it

**Parcel range shorthand** — in the Parcels field enter a Volume and a Folio range (e.g. `400-450`) then click **Add**. DocMate expands it to 51 individual `{volume_number, folio_number}` items automatically.

### 5. QA review (QA agent)

1. **My Tasks** → QA tasks appear for staff holding the QA shift role
2. Review the indexed data against the image; correct any fields directly in the form
3. **Submit & Complete** to pass (edits are saved and versioned) — or fail the record to send it back for indexing rework

### 6. Group records into a Lot and release to customer QC (Supervisor)

1. **Lots** → **New Lot**, select the QA-passed records to include
2. DocMate computes an AQL sample size/acceptance number for the lot; apply the sample
3. Release the lot — customer QC agents can now see it in the Customer Portal's **Lots** page, sample and pass/reject records
4. Rejected records re-enter the indexing workflow as rework (version 2+)

### 7. Monitor progress (Supervisor)

- **Staff Productivity** — per-agent metrics: records today, avg processing time, error rate
- **Project KPIs** — completion %, projected end date vs proposed, "Path to Completion" burn-up chart
- **Stale Tasks** — tasks overdue beyond the project's stale threshold; bulk or individual reassignment

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend API | Python 3.11 · FastAPI · async SQLAlchemy 2 · Alembic |
| Database | PostgreSQL 16 |
| Identity | Keycloak 24 (OIDC + PKCE via `keycloak-js`, one realm per tenant organisation) |
| File storage | MinIO (dev) / AWS S3 (prod) via `boto3` |
| Background jobs | APScheduler (stale task detection) |
| Frontend | React 18 · TypeScript · Vite (multi-page build) |
| UI components | Ant Design 5 (themed via `ConfigProvider` — single primary/slate palette) |
| Icons | lucide-react |
| Fonts | Inter (UI) · JetBrains Mono (machine identifiers), self-hosted via `@fontsource` |
| Forms | react-jsonschema-form (`@rjsf/antd`) |
| Charts | `@ant-design/charts` (AntV G2) |
| Data fetching | TanStack Query v5 |
| Image viewer | OpenSeadragon (deep zoom, PDF via `<iframe>`) |
| Split pane | react-split |
| Reverse proxy | nginx (TLS termination, per-subdomain routing, `X-Portal` header injection) |
| Containers | Docker Compose |

---

## Versioning & Releases

DocMate uses manual [SemVer](https://semver.org/). The `VERSION` file at the repo
root is the single source of truth; `CHANGELOG.md` tracks what's actually in each
release; `RELEASING.md` documents the steps to cut one (bump `VERSION`, update the
changelog, tag, build with the version stamped in).

A running instance reports its own version and git commit:

```bash
curl http://localhost:8000/version
# {"version": "0.2.0", "git_commit": "8e1011c"}
```

The same two values also appear in `/health`'s response and in the header of both
portals.

---

## Notes for Production

- Set a strong `SECRET_KEY` (minimum 32 random bytes)
- Replace MinIO with AWS S3 — update `AWS_*` environment variables and remove `S3_FORCE_PATH_STYLE`
- Set `KEYCLOAK_ADMIN_PASSWORD` to a strong password
- Replace the self-signed dev certificate (`nginx/certs/generate.sh <your-domain>`) with a real certificate (e.g. from your CA or Let's Encrypt) covering your domain and its wildcard subdomain
- Configure your domain via `.env` — set `CUSTOMER_DOMAIN`, `DE_HOSTNAMES`, `AUTH_HOSTNAME`, `CUSTOMER_PORTAL_BASE_URL`, and `DE_PORTAL_BASE_URLS` to your real hostnames (see `.env.example`)
- `AUTH_HOSTNAME` is also baked into the frontend JS bundles at build time (Vite's `VITE_KEYCLOAK_URL`), so changing it requires rebuilding those images — `docker compose up -d --build frontend-de frontend-cust` — a plain container recreate won't pick it up
- The `postgres_data` and `minio_data` Docker volumes hold all persistent data — back them up regularly
- The base `doc` realm's configuration is in `keycloak/realms/doc-realm.json` and is imported automatically on first start; customer realms are provisioned at runtime via the Keycloak Admin API and are **not** captured in that file — back up the Keycloak Postgres database (or export realms) separately
