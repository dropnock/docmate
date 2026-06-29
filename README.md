# DocMate

A multi-tenant document digitization management platform. DocMate manages the full lifecycle of physical document scanning projects — from batch ingestion and agent indexing through supervisor QA, AQL-based acceptance sampling, and customer quality control — with a complete audit trail and analytics dashboard.

---

## Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick Start (Docker)](#quick-start-docker)
- [Service URLs](#service-urls)
- [Default Credentials](#default-credentials)
- [Local Development](#local-development)
- [Configuration](#configuration)
- [Seeding Initial Data](#seeding-initial-data)
- [Key Workflows](#key-workflows)
- [Tech Stack](#tech-stack)

---

## Overview

DocMate operates two isolated portals:

| Portal | Audience | Purpose |
|--------|----------|---------|
| **Digitizing Entity** | Internal staff (supervisors, indexers, QA agents, admins) | Manage projects, batch ingestion, agent task assignment, indexing workspace, QA review, analytics |
| **Customer** | Client organisation staff (supervisors, QC agents) | Review completed batches, run QC sampling, accept or reject individual records |

Core features:

- **Multi-tenancy** — every table is scoped by `tenant_id`; no cross-tenant data leakage
- **Portal enforcement** — nginx injects an `X-Portal` header; the backend cross-checks it against the JWT `portal` claim on every request
- **Batch workflow state machine** — `draft → submitted → indexing → qa_review → customer_qc → passed | rejected`
- **Dynamic indexing forms** — per-project document type schemas (JSON Schema) drive fully auto-generated Ant Design forms, including range expansion for Volume/Folio parcel fields
- **Pessimistic record locking** — one agent owns a record at a time; 409 if locked by another user
- **Record versioning** — immutable snapshots (`RecordVersion`) on first submission and after customer rejection/rework
- **AQL acceptance sampling** — full ISO 2859-1 Inspection Level II table; auto-escalation (normal → tightened → reduced)
- **Full audit trail** — every state change, lock, assignment, and submission is logged to an append-only `audit_logs` table
- **Analytics** — staff productivity metrics, project KPI dashboards, burn-up charts
- **Stale task detection** — APScheduler job clears locks and flags overdue tasks every 15 minutes
- **S3-compatible file storage** — MinIO (dev) or AWS S3 (prod); presigned upload and view URLs

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                          nginx                               │
│   :80  → digitizing portal     :8080 → customer portal      │
│   Injects X-Portal header per server block                   │
└────────────┬──────────────────────────┬─────────────────────┘
             │                          │
    ┌────────▼──────────┐    ┌──────────▼──────────┐
    │  frontend-de       │    │  frontend-cust       │
    │  (React + Vite)    │    │  (React + Vite)      │
    │  Digitizing portal │    │  Customer portal     │
    └────────────────────┘    └──────────────────────┘
             │ /api/*                   │ /api/*
    ┌────────▼──────────────────────────▼─────────────────────┐
    │                     backend (FastAPI)                     │
    │   JWT auth · tenant scope · portal claim check           │
    └──────────┬──────────────────┬──────────────────┬────────┘
               │                  │                  │
        ┌──────▼──────┐   ┌───────▼──────┐   ┌──────▼──────┐
        │  PostgreSQL  │   │   MinIO/S3   │   │  Keycloak   │
        │  (primary DB)│   │ (file store) │   │  (OIDC/SSO) │
        └─────────────┘   └──────────────┘   └─────────────┘
```

### Backend layout

```
backend/app/
├── core/           # Config, async DB session, JWT/portal security
├── models/         # SQLAlchemy ORM models (all with tenant_id)
├── schemas/        # Pydantic request/response schemas
├── routers/        # FastAPI route handlers (never write business logic here)
├── services/       # All business logic
│   ├── audit_service.py      # write_event() — the only way to write audit logs
│   ├── batch_service.py      # state machine transitions
│   ├── lock_service.py       # acquire / release record locks
│   ├── version_service.py    # immutable RecordVersion snapshots
│   ├── task_service.py       # assign, reassign, bulk-reassign
│   ├── aql_service.py        # ISO 2859-1 sampling + escalation
│   ├── analytics_service.py  # productivity + KPI queries
│   └── s3_service.py         # bucket provisioning + presigned URLs
└── background/
    └── stale_checker.py      # APScheduler: stale task detection every 15 min
```

### Frontend layout

```
frontend/src/
├── portals/
│   ├── digitizing/pages/     # Supervisor + agent views
│   └── customer/pages/       # Customer QC views
└── shared/
    ├── components/
    │   ├── AgentWorkspace.tsx          # Split-screen indexing workspace
    │   ├── SchemaForm.tsx              # rjsf-driven dynamic form
    │   ├── rjsf/CustomWidgets.tsx      # RangeArrayField, ParcelArrayField, CountryWidget, DateTextWidget
    │   └── ImageViewer/                # OpenSeadragon deep-zoom viewer
    ├── api/                            # Axios client + TanStack Query hooks
    └── types/                          # Shared TypeScript interfaces
```

---

## Prerequisites

- **Docker** ≥ 24 and **Docker Compose** ≥ 2.20
- Ports `80`, `8080`, `8180`, `8000`, `5173`, `5174`, `5432`, `9000`, `9001` available

---

## Quick Start (Docker)

```bash
# 1. Clone
git clone https://github.com/dropnock/docmate.git
cd docmate

# 2. (Optional) Set a strong secret key
echo "SECRET_KEY=$(openssl rand -hex 32)" > .env

# 3. Start everything
docker compose up --build

# First run takes ~2 minutes while Keycloak imports the realm.
# Wait until you see: "Application startup complete." in the backend logs.

# 4. Seed the initial tenant, organisations, and users
docker compose exec backend python seed.py
```

> **Subsequent starts** (no rebuild needed):
> ```bash
> docker compose up
> ```

> **After any backend code change:**
> ```bash
> docker compose build backend && docker compose up -d backend
> ```

> **After any frontend code change:**
> ```bash
> docker compose build frontend-de frontend-cust && docker compose up -d frontend-de frontend-cust
> ```

---

## Service URLs

| Service | URL | Notes |
|---------|-----|-------|
| **Digitizing Portal** | http://localhost | Main staff portal |
| **Customer Portal** | http://localhost:8080 | Client QC portal |
| **Backend API** | http://localhost:8000 | FastAPI |
| **API Docs (Swagger)** | http://localhost:8000/docs | Interactive API explorer |
| **Keycloak Admin** | http://localhost:8180 | Identity provider admin |
| **MinIO Console** | http://localhost:9001 | S3-compatible object store UI |
| **Direct DE frontend** | http://localhost:5173 | Bypasses nginx (dev use) |
| **Direct Customer frontend** | http://localhost:5174 | Bypasses nginx (dev use) |

---

## Default Credentials

### Application users (Keycloak — `doc` realm)

> Created by `seed.py`. All passwords are `changeme123`.

| Email | Role | Portal |
|-------|------|--------|
| `admin@doc.local` | Admin | Digitizing |
| `supervisor@doc.local` | DE Supervisor | Digitizing |
| `indexer@doc.local` | DE Indexer | Digitizing |
| `qa@doc.local` | DE QA Agent | Digitizing |
| `supervisor@acme.local` | Customer Supervisor | Customer |
| `qc@acme.local` | Customer QC Agent | Customer |

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
pytest tests/test_aql.py   # single file
```

---

## Configuration

Backend is configured via environment variables (see `backend/.env.example`):

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL async connection string | `postgresql+asyncpg://docmate:docmate@localhost:5432/docmate` |
| `SECRET_KEY` | JWT signing secret — **change in production** | `change-me-in-production` |
| `AWS_ACCESS_KEY_ID` | S3 / MinIO access key | `minioadmin` |
| `AWS_SECRET_ACCESS_KEY` | S3 / MinIO secret key | `minioadmin` |
| `AWS_ENDPOINT_URL` | S3 endpoint (internal) | `http://localhost:9000` |
| `AWS_PUBLIC_ENDPOINT_URL` | S3 endpoint for presigned URLs (browser-accessible) | `http://localhost:9000` |
| `AWS_REGION` | S3 region | `us-east-1` |
| `S3_FORCE_PATH_STYLE` | Required for MinIO | `true` |
| `KEYCLOAK_INTERNAL_URL` | Keycloak URL for backend token validation | `http://keycloak:8080` |
| `KEYCLOAK_EXTERNAL_URL` | Keycloak URL for browser redirects | `http://localhost:8180` |
| `KEYCLOAK_ADMIN_USER` | Keycloak admin username | `admin` |
| `KEYCLOAK_ADMIN_PASSWORD` | Keycloak admin password | `admin` |

---

## Seeding Initial Data

The `seed.py` script creates:

- Tenant: **Digitizing Operations Centre** (slug: `doc`)
- Digitizing org: **DOC**
- Customer org: **Acme Archive Corp** (Keycloak realm: `acme-archive`)
- Six users across both portals (see [Default Credentials](#default-credentials))

```bash
# Docker
docker compose exec backend python seed.py

# Local
cd backend && python seed.py
```

---

## Key Workflows

### 1. Create a project and document type (Admin)

1. Log in as `admin@doc.local` → **Projects** → **New Project**
2. Inside the project → **Batches** tab → **Document Types** tab → **New Document Type**
3. Paste a JSON Schema describing the indexing form fields

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

### 2. Upload documents and create a batch (Admin/Supervisor)

1. **Batches** tab → **New Batch** → select document type
2. Click **Records** → **Upload Images** — drag and drop JPEG/PNG/TIFF/PDF files
3. Each file automatically creates one record linked to that file in S3
4. Click **Submit for Indexing** to advance the batch

### 3. Assign records to indexers (Supervisor)

1. **Task Assignment** → select batch and shift
2. Choose an agent per record, or use **Auto-assign (round-robin)** for the whole batch

### 4. Index a record (Indexer)

1. **My Tasks** → click **Start Indexing** on an assigned task
2. The split-screen workspace opens: document image (left) + auto-generated form (right)
3. Fill in the form; use **Save Progress** to save without completing
4. Click **Submit & Complete** to submit and release the record lock

**Parcel range shorthand** — in the Parcels field enter a Volume and a Folio range (e.g. `400-450`) then click **Add**. DocMate expands it to 51 individual `{volume_number, folio_number}` items automatically.

### 5. QA Review → Customer QC (Supervisor)

1. Once all records are indexed, advance the batch through **QA Review** → **Customer QC**
2. Customer QC agents log into the Customer Portal to sample and pass/reject records
3. Rejected records re-enter the indexing workflow as rework (version 2+)

### 6. Monitor progress (Supervisor)

- **Staff Productivity** — per-agent metrics: records today, avg processing time, error rate
- **Project KPIs** — completion %, projected end date vs proposed, burn-up chart
- **Stale Tasks** — tasks overdue beyond the project's stale threshold; bulk or individual reassignment

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend API | Python 3.11 · FastAPI · async SQLAlchemy 2 · Alembic |
| Database | PostgreSQL 16 |
| Identity | Keycloak 24 (OIDC + PKCE, per-tenant realms) |
| File storage | MinIO (dev) / AWS S3 (prod) via `boto3` |
| Background jobs | APScheduler (stale task detection) |
| Frontend | React 18 · TypeScript · Vite (multi-page build) |
| UI components | Ant Design 5 |
| Forms | react-jsonschema-form (`@rjsf/antd`) |
| Data fetching | TanStack Query v5 |
| Image viewer | OpenSeadragon (deep zoom, PDF via `<iframe>`) |
| Split pane | react-split |
| Reverse proxy | nginx |
| Containers | Docker Compose |

---

## Notes for Production

- Set a strong `SECRET_KEY` (minimum 32 random bytes)
- Replace MinIO with AWS S3 — update `AWS_*` environment variables and remove `S3_FORCE_PATH_STYLE`
- Set `KEYCLOAK_ADMIN_PASSWORD` to a strong password and update `KC_HOSTNAME_URL` to your domain
- Configure TLS termination at the nginx layer
- The `postgres_data` and `minio_data` Docker volumes hold all persistent data — back them up regularly
- Keycloak realm configuration is in `keycloak/realms/doc-realm.json` and is imported automatically on first start
