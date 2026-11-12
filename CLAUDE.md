# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Backend
```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt  # first time
source .venv/bin/activate

# Run dev server (requires postgres + minio running)
uvicorn app.main:app --reload --port 8000

# Migrations
alembic upgrade head
alembic revision --autogenerate -m "description"   # needs live DB

# Tests
pytest
pytest tests/test_aql.py                            # single file
```

### Frontend
```bash
cd frontend
npm install                         # first time
npx vite --port 5173                # digitizing portal dev server
npx vite --port 5174                # customer portal dev server (same vite, different entry)
npx tsc --noEmit                    # type check
npx vite build                      # production build → dist/
```

### Docker (full stack)
```bash
docker-compose up --build           # start everything
docker-compose up postgres minio    # just dependencies for local dev
docker-compose logs -f backend
```

### URLs (docker)
- Digitizing portal: http://localhost:80
- Customer portal: http://localhost:8080
- Backend API docs: http://localhost:8000/docs
- MinIO console: http://localhost:9001  (minioadmin / minioadmin)

## Architecture

### Multi-tenancy
Every DB table has `tenant_id`. `get_current_user()` in `core/security.py` decodes the JWT, attaches `_tenant_id` to the user object, and all service functions receive it as a parameter — queries are always filtered by it. No cross-tenant leakage is possible at the query layer.

### Portal enforcement
Two separate portals (Digitizing Entity / Customer) with distinct firewall rules. nginx injects `X-Portal: digitizing|customer` per server block. The backend `get_current_user()` dependency cross-checks this header against the JWT `portal` claim and raises 403 on mismatch.

### Batch workflow state machine
`batch_service.py` owns all state transitions — never write `batch.status` directly outside it:
```
draft → submitted → indexing → qa_review → customer_qc → passed | rejected
```
Each transition is an explicit method (`submit_batch`, `advance_to_indexing`, etc.) that emits an audit event.

### AQL engine (`services/aql_service.py`)
Full ISO 2859-1 Inspection Level II lookup table. `compute_sample_size(batch_size, aql_level)` returns `(sample_size, acceptance_number)`. `evaluate_batch()` compares defects to the acceptance number, updates `AQLConfig.current_status` (normal → tightened → reduced), and emits a `batch_escalated` audit event on escalation.

### Outgoing lot inspection (`services/lot_inspection_service.py`)
Digitizing supervisor's ISO 2859-1 inspection of a **draft** lot before handover (separate from the customer's own post-release sampling in `lot_service.apply_sample`). The supervisor picks an AQL; `aql_service.sampling_plan()` gives code letter / n / Ac / Re, and the sample is drawn with `random.Random(random_seed)` (seed stored, so the draw is reproducible). The supervisor marks every field of each sampled record; `complete_inspection()` fails the lot on any critical-field defect or when non-critical defects exceed Ac, and requeues every defective record to indexing either way. `lot_service.release_lot` calls `assert_releasable()`: the latest non-cancelled attempt must have passed and all lot records must be `qa_passed` again. A failed lot is re-inspected with a fresh sample once its rework is back. The report (`GET /api/lots/{id}/inspection-report`) is visible to the customer supervisor only after release. **Recall** (`lot_service.recall_lot`, `POST /lots/{id}/recall`, reason required): a released lot the customer hasn't sampled goes back to draft; the lot row is locked (`_get_lot_for_update`, also taken by `apply_sample`) so recall and sampling can't both win. Recalling, or removing records from a draft lot (`remove_lot_records`), marks the passing inspection `superseded`, which `_latest_effective` ignores — so a fresh inspection is needed before release. Audit events use `AuditEntityType.lot`.

### Customer access to lots
Every lot endpoint goes through `routers/lots.py::_get_authorized_lot`: tenant + `check_project_access` (a tenant holds several customer organisations) and, for the customer portal, 404 on draft lots. `list_lots` hides drafts from customers server-side. `create_qc_batches` checks that the body's project, document type and agents belong to the lot's project and its customer organisation.

### Record locking (`services/lock_service.py`)
Pessimistic lock: `acquire_lock()` is called when an agent starts a task; raises `409` with `{locked_by, locked_at}` if another user holds it. `release_lock()` is called on task complete, fail, or reassign.

### Record versioning (`services/version_service.py`)
`create_version()` snapshots `record.indexed_data` into an immutable `RecordVersion` row and increments `record.current_version`. Called in two places only:
1. `task_service.complete_task()` — on first indexing submission (v1) or rework (v2+)
2. `batch_service.reject_record_by_customer()` — when customer QC rejects a specific record

### Audit trail (`services/audit_service.py`)
`write_event()` is the only way to write to `audit_logs`. Call it from service methods, never from routers. It does not commit — the caller owns the transaction, keeping the audit entry atomic with the operation it records. Use the entity type of the thing the event is about (`record`, `lot`, `cabinet`, `batch`, …) with that thing's id — `get_record_history()` (one record) and `get_lot_history()` (one lot, `GET /api/lots/{id}/history`; customers see it from first release onward) filter on it, so an event filed under the wrong type leaks into the history of an unrelated record that shares the id.

### Analytics (`services/analytics_service.py`)
- `staff_productivity()` — per-agent metrics, filterable by shift/date
- `project_kpis()` — completion %, projected vs proposed end date, on-track flag
- `burnup_chart_data()` — 30-day actual cumulative + forward projection series

### Frontend — two portals, one codebase
Vite multi-page build with two entry points (`index-digitizing.html`, `index-customer.html`). Portal is detected at runtime from `window.location.hostname`. Shared code lives in `src/shared/`; portal-specific pages under `src/portals/digitizing/` and `src/portals/customer/`.

### Agent Workspace (`shared/components/AgentWorkspace.tsx`)
Split-screen via `react-split`. Left pane: OpenSeadragon deep-zoom image viewer (scroll-wheel zoom, mouse-drag pan). Right pane: `SchemaForm` — a dynamic Ant Design form driven entirely by `DocumentType.json_schema`. Acquiring a task via `POST /api/tasks/{id}/start` automatically acquires the record lock. A banner shows lock status; rework records pre-fill the form from `record.indexed_data`.

## Key conventions
- Commit after every feature and bugfix: `feat(<scope>): …` / `fix(<scope>): …`
- All audit writes via `audit_service.write_event()` only
- All batch status changes via `batch_service` methods only
- All record lock changes via `lock_service` methods only
- S3 bucket name format: `docmate-{tenant_slug}-{project_slug}`
- Backend virtualenv: `backend/.venv/` (not committed)
- Frontend `node_modules/`: not committed
