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
ISO 2859-1 single sampling at the project's **general inspection level** (`AQLConfig.general_level` I / II / III, default II; picks the Table 1 lot-size → code-letter column, set by the customer supervisor in Lot Settings, snapshotted on `LotInspection.general_level` and named in the report's `standard`): the normal, tightened and reduced master tables (2-A / 2-B / 2-C; reduced per the 1999 edition, Re = Ac + 1) for AQLs 0.65 / 1.0 / 1.5 / 2.5, with arrow cells resolved to the row they point at (whose sample size then applies). `sampling_plan(lot_size, aql, level)` (exact AQL) and `compute_sample_size(batch_size, aql, level)` (snaps to the nearest column) give n / Ac; callers cap n at the lot size. The project has one AQL (`AQLConfig.normal_aql`, validated against the table; `tightened_aql` / `reduced_aql` are no longer read) — the **inspection level** picks the table, not a different AQL. **Each lot's level is set by the digitizing supervisor** when starting its outgoing inspection (`Lot.inspection_level`, `LotInspection.inspection_level`); the customer's ISO sample (`lot_service.apply_sample`) uses the same level (falling back to the project suggestion for lots inspected before levels existed) and freezes it in `Lot.aql_status_snapshot`. `AQLConfig.current_status` is only the **suggested** level (`get_suggested_level`), moved by `apply_switching()` per `failures_to_tighten` / `passes_to_reduce` (normal→tightened after N consecutive rejections, tightened→normal and normal→reduced after N consecutive acceptances, reduced→normal on any rejection; each switch resets the counters). `lot_service.calculate_accuracy` feeds each lot verdict in through `record_lot_outcome()` — first QC round only, so resubmitted rounds never count — which writes a project audit event on a switch (`batch_escalated` when tightening). Customers see the inspection level, never the AQL number, on lots and in the inspection report (`build_report(include_aql=False)`). The project KPI error rate is QC-rejected ÷ QC-reviewed records (QC task outcomes, all rounds). The legacy `evaluate_batch()` / `BatchQCResult` path is unreachable.

### Defect classes (`services/defect_class_service.py`)
Both the outgoing inspection and customer QC class each defective field as one critical / major / minor non-conformity. An error in an identifier field (`"x-critical": true` in the schema; ticked under "Identifier fields" in the document-type editor) is critical; any other field's error takes the class the project's agreement gives its error type (`AQLConfig.error_classes`: missing → major, typo / interpretation → minor by default; only major/minor allowed). Reviewers must give an error type (`FieldResultIn.error_type`) for each defective field; `error_type` and `severity` are frozen on the mark. The project has an AQL per class (`critical_aql` — NULL = zero tolerance —, `normal_aql` = the **major** AQL, exposed as `major_aql`, `minor_aql`; AQLs 0.65–6.5). `aql_service.class_plans()` draws one sample at the largest size any class's own plan needs and reads each class's Ac at that size (`_ac_at`: never more lenient than its own plan); the plan is stored as `class_plan` on `LotInspection` / `Lot` / `LotQcRound`. `judge()` accepts the lot only when every class's total is within its Ac. `class_plan` NULL (inspections / samples from before classes) keeps the old rule: any critical fails, other defects together against `acceptance_number`. `acceptance_number` / `aql_level` hold the major class's values. The three AQLs are set on the digitizing portal's project create / edit forms (`POST`/`PATCH /api/projects`, via `project_service`) as well as the customer's Lot Settings (`PATCH /projects/{id}/aql`); every change is a `settings_changed` project audit event (old → new), shown by `GET /api/projects/{id}/history` (admin, de_supervisor). Changing them never touches a started inspection or sample, which judge from their own `class_plan`.

### Outgoing lot inspection (`services/lot_inspection_service.py`)
Digitizing supervisor's ISO 2859-1 inspection of a **draft** lot before handover (separate from the customer's own post-release sampling in `lot_service.apply_sample`). The supervisor picks the inspection level (normal / tightened / reduced — the list endpoint returns the suggested level and each level's plan for the lot); the AQLs are the project's (see Defect classes). `aql_service.class_plans()` gives code letter / n / per-class Ac / Re, and the sample is drawn with `random.Random(random_seed)` (seed stored, so the draw is reproducible). The supervisor marks every field of each sampled record; `complete_inspection()` judges the per-class totals, and requeues every defective record to indexing either way. `lot_service.release_lot` calls `assert_releasable()`: the latest non-cancelled attempt must have passed and all lot records must be `qa_passed` again. A failed lot is re-inspected with a fresh sample once its rework is back. The report (`GET /api/lots/{id}/inspection-report`) is visible to the customer supervisor only after release. **Recall** (`lot_service.recall_lot`, `POST /lots/{id}/recall`, reason required): a released lot the customer hasn't sampled goes back to draft; the lot row is locked (`_get_lot_for_update`, also taken by `apply_sample`) so recall and sampling can't both win. Recalling, or removing records from a draft lot (`remove_lot_records`), marks the passing inspection `superseded`, which `_latest_effective` ignores — so a fresh inspection is needed before release. **Resubmit** (`lot_service.resubmit_lot`, `POST /lots/{id}/resubmit`): once a `remediation` lot's rejected records are back through QA, its finished customer QC round is archived as a `LotQcRound`, its live sample/result fields are cleared, `qc_passed` records go back to `qa_passed`, `Lot.qc_round` is incremented, and it returns to draft with the old inspection superseded. `QcFieldResult.qc_round` keeps rounds apart: `calculate_accuracy` and the tabulation only read the current round. A record belongs to at most one lot (`create_lot` check + unique index on `lot_records.record_id`). Audit events use `AuditEntityType.lot`.

### Customer QC decision (`lot_service.calculate_accuracy` / `override_qc_mark` / `decide_lot`)
The lot verdict is never automatic. Customer QC batches (`BatchType.qc`) stay open (status `indexing`) after their records are reviewed: `GET /tasks/mine` keeps listing their QC tasks, and `task_service.start_task` reopens a finished one (`qc_task_reopenable`: batch open, record in a sampled lot still `qc_in_progress`), with the new marks replacing the task's old `QcFieldResult` rows. The reviewer hands the batch on with `lot_service.submit_qc_batch` (`POST /batches/{id}/submit-qc`, also the customer supervisor's), which needs every task finished, sets the batch `complete` and calls `calculate_accuracy`. When the last sampled record's QC task is finished and none of its QC batches is still open (`_open_qc_batch_ids`), `calculate_accuracy` moves the lot from `qc_in_progress` to `awaiting_decision` and stores the provisional counts. The customer QC supervisor reviews the sample (`GET /lots/{id}/qc-review`, page `/lots/:lotId/review`).
- `override_qc_mark` stores their ruling beside the reviewer's mark in `QcFieldResult.override_*`. The reviewer's mark is never changed. A ruling that matches the reviewer's clears the override. Any change needs a note.
- `defect_class_service.effective()` is the mark that counts. Everything that tallies marks (`_tally`/`_judge`, the tabulation) must use it.
- The record's status follows its effective marks. Manual-rate lots use `override_record_outcome` (a whole-record ruling) instead.
- `decide_lot` (`POST /lots/{id}/decide`) applies the verdict, sets `qc_completed_at`/`decided_*`, calls `record_lot_outcome` (round 1 only), and only then calls `task_service.route_to_rework` for each record still `qc_failed`.
- `fail_task` doesn't create rework for sampled lot records, only for QC tasks outside any lot.

### Customer access to lots
Every lot endpoint goes through `routers/lots.py::_get_authorized_lot`: tenant + `check_project_access` (a tenant holds several customer organisations) and, for the customer portal, 404 on draft lots. `list_lots` hides drafts from customers server-side. `create_qc_batches` checks that the body's project, document type and agents belong to the lot's project and its customer organisation.

### Record holds (`services/hold_service.py`)
A digitizing supervisor holds pending, unbatched records back from assignment (reason required; `record_holds`, active while `released_at` is null, one active hold per record). `cabinet_service.create_indexing_batch` and `task_service.assign_task` call `hold_service.require_not_held()` (409); Cabinet Assignment loads its picker with `exclude_held=true`. Hold/cancel are audited on the record (`record_held` / `hold_released`).

### Flags shared with the customer (`services/flag_service.py`, `services/notification_service.py`)
A digitizing supervisor shares open flags with the project's customer supervisors (`RecordFlag.shared_at/shared_by`). A customer-portal user only ever sees shared flags: `routers/flags.py::_flag` 404s otherwise, `GET /projects/{id}/flags` returns `shared_only`, and `GET /records/{id}/history` drops flag events of unshared flags (`flag_service.is_hidden_flag_event`). Both sides discuss a shared open flag in `flag_comments`. Sharing, each comment, resolving/withdrawing a shared flag and unsharing call `notification_service.notify_project_supervisors()` — active DE supervisors of the project's digitizing org (or no org) plus customer supervisors of its customer org, minus the actor — which writes `notifications` rows in the caller's transaction (header bell, `GET /api/notifications`). `GET /projects/{id}/flags/export` downloads the list as CSV and writes `flags_exported` on the project.

### Record locking (`services/lock_service.py`)
Pessimistic lock: `acquire_lock()` is called when an agent starts a task; raises `409` with `{locked_by, locked_at}` if another user holds it. `release_lock()` is called on task complete, fail, or reassign.

### Record versioning (`services/version_service.py`)
`create_version()` snapshots `record.indexed_data` into an immutable `RecordVersion` row and increments `record.current_version`. Called in two places only:
1. `task_service.complete_task()` — on first indexing submission (v1) or rework (v2+)
2. `batch_service.reject_record_by_customer()` — when customer QC rejects a specific record

### Audit trail (`services/audit_service.py`)
`write_event()` is the only way to write to `audit_logs`. Call it from service methods, never from routers. It does not commit — the caller owns the transaction, keeping the audit entry atomic with the operation it records. Use the entity type of the thing the event is about (`record`, `lot`, `cabinet`, `batch`, …) with that thing's id — `get_record_history()` (one record) and `get_lot_history()` (one lot, `GET /api/lots/{id}/history`; customers see it from first release onward) filter on it, so an event filed under the wrong type leaks into the history of an unrelated record that shares the id.

### Presence (`services/presence_service.py`)
`get_current_user` calls `record_request()`, which stamps `User.last_seen_at` at most once a minute in its own committed transaction (never the request's, so the users row isn't held locked) and starts a new `session_started_at` after a 30-minute gap; it uses a Core UPDATE that leaves `updated_at` alone and never fails the request. Open tabs poll, so "seen" means DocMate is open; "working" comes from the open task's `last_active_at` (task heartbeat). `GET /api/users/online?within=<minutes>` (admin) backs the Who's Online page. Tests set `USE_OWN_SESSION = False` in conftest.

### Analytics (`services/analytics_service.py`)
- `staff_productivity()` — per-agent metrics, filterable by shift/date
- `project_kpis()` — completion %, projected vs proposed end date, on-track flag
- `burnup_chart_data()` — 30-day actual cumulative + forward projection series
- `staff_task_times()` — one agent's time per record (`GET /api/analytics/staff-productivity/tasks`, `format=csv`), the drill-down behind Avg Time; same roles as the report it details (QC → customer supervisor/admin)

### Task timing (`services/task_timing.py`)
`processing_time_seconds` is active time, added up across sessions. The workspaces call `useTaskHeartbeat` → `POST /tasks/{id}/heartbeat`: `opened=true` on opening (resuming a task already in progress never calls start_task, so this is where its session is counted), then every 30s while the tab is visible and the agent has given input in the last 5 minutes, plus on return from idle/hidden and on leaving; each beat adds the gap since the last one to `Task.active_seconds` unless the gap is over 90s (time away). `start_task` → `begin_session` (counts `sessions`; a start within 90s of a beat continues the session), complete/fail/skip/requeue → `end_session`, reassign → `reset`. Tasks with no `last_active_at` keep the old measure (last open → finish). Don't set `started_at` / `processing_time_seconds` directly.

### Required fields (`services/required_fields.py`, `shared/components/requiredRules.ts`)
A field named in a schema `required` array (any depth) must have a value: not missing/null, text not blank, a list with at least one non-blank entry, an object with at least one value; `0`/`false` count. Item-level `required` applies to every entry that exists; `x-hidden` fields are exempt. The form enforces it via RJSF `customValidate`; `complete_task` re-checks every indexing/QA completion (the submitted data, or the record's saved data when none is sent) and 422s with `{message, missing}`. Save Progress saves partial data but warns which required fields are still empty. JSON import can't check: records have no document type until batched. The two implementations must agree — both run `backend/tests/fixtures/required_fields_cases.json`.

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

## gstack

Use the `/browse` skill from gstack for all web browsing. Never use
`mcp__claude-in-chrome__*` tools.

Available skills: `/office-hours`, `/plan-ceo-review`, `/plan-eng-review`,
`/plan-design-review`, `/design-consultation`, `/design-shotgun`,
`/design-html`, `/review`, `/ship`, `/land-and-deploy`, `/canary`,
`/benchmark`, `/browse`, `/connect-chrome`, `/qa`, `/qa-only`,
`/design-review`, `/scrape`, `/setup-browser-cookies`, `/setup-deploy`,
`/setup-gbrain`, `/retro`, `/investigate`, `/document-release`,
`/document-generate`, `/codex`, `/cso`, `/autoplan`, `/plan-devex-review`,
`/devex-review`, `/careful`, `/freeze`, `/guard`, `/unfreeze`,
`/gstack-upgrade`, `/learn`.
