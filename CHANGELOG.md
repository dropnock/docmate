# Changelog

All notable changes to DocMate are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/).

See `RELEASING.md` for how to cut a release.

## [Unreleased]

## [0.6.0] - 2026-08-13

### Added
- **Customer portal: Lot Settings / Assign to QC split** — replaces the
  single "Manage" lot screen with dedicated Settings (ISO 2859-1 sampling
  configuration, editable AQL config, computed sample size, which records
  were chosen) and Assign to QC screens. Lot sampling now has a per-project
  `sampling_mode`: ISO-computed (default) or manual rate.
- **Per-field QC defect marking** (ISO 2859-1) — customer QC agents on
  ISO-sampling projects can mark individual record fields accepted/defective
  instead of only passing/rejecting the whole record. Fields can be flagged
  critical (`"x-critical": true` in a document type's schema); a critical
  defect fails the lot outright, other defects are tabulated against the
  lot's ISO 2859-1 acceptance number. New supervisor-facing defect
  tabulation view on the lot's Settings screen.
- **Customer Supervisor QC reporting dashboard** — dedicated Project KPIs
  page: lots quality-checked, lots rejected, records passed, QC team daily
  throughput, and a Lots Processed table (release date, AQL inspection
  level at time of sampling, QC completion date, pass/fail, critical/minor
  defect counts).
- Records: search by filename on the Review & Requeue tab.
- `scripts/migrate_records_to_training.py` — copies a sample of records
  from one environment into a cabinet in another (e.g. a training
  environment).
- `scripts/export_caveat_volume_folio.py` — CSV export of caveat/volume/
  folio for `qa_passed` records, including each record's image filename.
- `scripts/generate_qc_training_data.py` — bulk-advances `qa_pending`
  records to `qa_passed` through the real QA-completion path, to generate
  customer QC training data without fabricating audit history.
- `scripts/rollback_lot.py` — rolls back a draft/released lot's creation.
- `scripts/requeue_qa_passed_records.py` — bulk-sends `qa_passed` records
  back through QA.

### Fixed
- Customer QC agents could reach Lots, Project KPIs, and Record History by
  direct URL despite the tabs being hidden — now role-gated on the
  underlying `GET /lots/...` and `GET /records/{id}/history` endpoints too,
  not just in the UI.
- `AQLConfig` was looked up by primary key instead of its `project_id`
  column, silently returning the wrong config (or none) outside of
  coincidentally-aligned seed data — affected the AQL escalation logic
  already live in production.
- The ISO 2859-1 code-letter table's bucket boundaries were shifted one row
  off from the real standard, producing the wrong sample size/acceptance
  number for every AQL evaluation.
- Lot pass/fail (`calculate_accuracy`) ignored `Lot.acceptance_number` and
  always applied a flat 90%-pass-rate rule, even for ISO-sampled lots where
  a real acceptance number had already been computed.

## [0.5.1] - 2026-08-04

### Added
- **Supervisor record review & requeue** (Records Management → "Review &
  Requeue" tab, `de_supervisor`/`admin`) — project-wide, status-filterable
  record list; view a record's image via the existing backend proxy; see
  who most recently indexed and QA'd each record; send selected records
  back for re-indexing or a fresh internal QA pass; export selected
  records' current data as a ZIP of one JSON file per record.
- CSV export of raw per-user error rates (`scripts/export_user_error_rates.py`).
- `scripts/backup_database.sh`.

### Fixed
- Container Infrastructure Grafana dashboard: `cadvisor` now runs
  privileged with a `/dev/kmsg` mount, fixing CPU Usage, Memory Usage,
  Container Uptime, and Network Usage — All Containers, which were coming
  back blank.
- Records sent back for re-indexing now reset to `status=pending`,
  detached from any batch, instead of `qa_failed` inside a dedicated
  one-off rework batch — the old state had no assignment UI anywhere.
  `scripts/backfill_supervisor_requeue_status.py` corrects any records
  already stuck in the old state.

### Security
- Migrated `react-router-dom` v6 to `react-router` v7, closing 3
  Dependabot alerts (GHSA-wrjc-x8rr-h8h6, GHSA-jjmj-jmhj-qwj2,
  GHSA-337j-9hxr-rhxg) — the `react-router-dom` 6.x line had no patch
  available for one of them; the fix only ever landed in the unified
  `react-router` package.

## [0.5.0] - 2026-07-30

### Added
- **Records CSV export** (`scripts/export_records_report.py`) — one-off
  report of every record's current status, resolved project/cabinet/batch,
  and lock holder. Streams results in pages by record id rather than
  loading the whole table; filterable by `--tenant-id`, `--project-id`,
  `--status`.

### Fixed
- Per-agent error rate (`GET /analytics/staff-productivity`) was always 0%
  — `task_service.fail_task()` was writing `TaskStatus.completed` instead
  of `TaskStatus.failed` on QA/QC rejection, even though its own audit log
  call already recorded the correct outcome. `scripts/
  backfill_task_failed_status.py` retroactively corrects historical tasks
  from the audit trail.
- Project-level error rate wasn't tracked at all. `GET /analytics/
  project-kpis/{id}` now reports `error_rate`/`defects_found`/
  `records_inspected`, aggregated from AQL `BatchQCResult`s, and shown on
  the Project KPIs dashboard.

### Security
- Pinned `fast-uri` to 3.1.4, fixing GHSA-v2hh-gcrm-f6hx (host confusion
  via backslash authority delimiter, CVSS 7.5) — transitive via
  `@rjsf/utils`/`ajv`.
- Pinned `postcss` to 8.5.23, fixing GHSA-r28c-9q8g-f849 (path traversal
  disclosing arbitrary `.map` files, CVSS 7.5) — transitive via `vite`.

## [0.4.1] - 2026-07-25

### Removed
- Stale-task auto-flagging, end to end: the 15-minute APScheduler background
  job, `TaskStatus.stale`, `AuditAction.stale_flagged`/`lock_expired`,
  `Task.due_at`, `Project.stale_threshold_hours`, the "Stale Tasks"
  supervisor page, `GET /tasks/stale`, and the per-agent `stale_task_count`
  productivity metric. It wasn't having its intended effect; overdue tasks
  now simply stay `pending`/`in_progress` until a supervisor manually
  reassigns them (unchanged: `PATCH /tasks/{id}/reassign`,
  `POST /tasks/bulk-reassign`). A hand-written migration narrows the
  `taskstatus`/`auditaction` Postgres enums and drops the two columns.

### Added
- **Records Management** (digitizing portal, supervisors/admins) — replaces
  the old "Record History" page. A Dashboard tab shows batches
  indexed/QA'd, total/withdrawn/illegible records, optionally filtered by
  date range. A History tab lists batches with status/date-range filtering,
  totals, per-batch reassignment of indexing/QA work to a different agent,
  and a drill-down into a batch's records (audit trail, versions, and
  force-unlock for a locked record).

### Fixed
- Records dashboard date-range filters (`GET /analytics/records-dashboard`)
  now accept full timestamps instead of plain dates, matching
  `list_batches`' own param type — the previous truncation silently
  excluded any batch still in progress from a date-filtered dashboard.
  Failed dashboard/history fetches now also surface as a toast instead of
  silently rendering empty-state zeros.

### Security
- Fixed several endpoints that resolved a record/batch/task/AQL-config
  purely by its integer ID with no tenant or project ownership check,
  letting any authenticated user (any tenant, any role) read or, in some
  cases, write another tenant's data: `GET /records/{id}`,
  `PATCH /records/{id}/draft`, `GET /records/{id}/versions`,
  `POST /records/{id}/unlock`, `GET /projects/{id}/aql`, and the
  task-assignment endpoints (`POST /tasks/assign`,
  `PATCH /tasks/{id}/reassign`, `POST /tasks/bulk-reassign`,
  `PATCH /batches/{id}/assign-qa`). All now enforce `check_project_access`,
  the same tenant/portal boundary already used elsewhere in the API.
- Portal enforcement (`X-Portal` header vs. JWT `portal` claim) now fails
  closed — a missing header is rejected rather than skipped — and the
  backend's Docker port is bound to loopback only, closing a path that let
  a client bypass nginx (the only party meant to set `X-Portal`) entirely.
- Removed `verify=False` from the Keycloak JWKS fetch, which disabled TLS
  certificate validation on the call that retrieves JWT signing keys.
- Cabinet image uploads now sanitize the client-supplied filename before
  it's used to build the S3 object key, closing a path-traversal risk in
  the shared per-org bucket.

## [0.4.0] - 2026-07-21

### Added
- Structured JSON logging with a per-request `X-Request-ID`, propagated
  through validation-error and unhandled-exception handlers so a
  client-visible error can be traced straight to its server-side log line.
  Frontend `WorkspaceErrorBoundary` now reports caught render errors to a
  new `POST /api/client-errors` endpoint instead of only being visible in
  whoever's browser hit the crash.
- Prometheus `/metrics` endpoint (prometheus-fastapi-instrumentator) plus
  custom counters for record-lock conflicts and stale-task processing, and
  a DB-aware `/health` check.
- Full observability stack in `docker-compose.yml`: Prometheus, Loki,
  Promtail, Grafana, cAdvisor (per-container CPU/memory/network/uptime),
  and a small custom volume-exporter sidecar (cAdvisor can't see data
  inside a mounted named volume, only each container's own writable
  layer). Grafana also gets a read-only Postgres datasource, reusing the
  backend's own DB credentials, for dashboards that need direct access to
  app data rather than request/latency metrics.
- Grafana dashboard: **Digitization Team Productivity** — per-project
  ($project variable) records indexed/QA'ed/released, lots released,
  quality rate, completion %, current/required daily throughput,
  projected vs. proposed completion dates with an on-track status tile,
  a 30-day actual-vs-projected completion trend, and per-user
  productivity (Indexing in blue, Quality/QA in green). Mirrors
  `analytics_service.py`'s existing `project_kpis()`/`burnup_chart_data()`
  formulas so it can't silently disagree with what the app reports.
- Grafana dashboard: **Container Infrastructure** — CPU, memory, and
  uptime per container; real per-volume disk usage; network usage per
  container plus a dedicated external-traffic panel for nginx, the sole
  external-facing ingress/egress in this architecture.

### Fixed
- `stale_checker.py`'s `_tenant_id_for_task` was a hardcoded placeholder
  returning 0, violating `audit_logs.tenant_id`'s FK constraint and
  aborting the whole stale-check run's transaction — no tasks got flagged
  or unlocked. `tenant_id` is now resolved once per task via
  batch→project, before either write, same as the run's other audit event
  already did.

## [0.3.6] - 2026-07-20

### Fixed
- Batches could be created with zero records. `create_indexing_batch`'s
  eligibility check only validated *requested* record ids that turned out
  ineligible — an empty `record_ids` list vacuously passed, so a `Batch`
  row was created with no `Task`/`Record` rows attached. Now rejects an
  empty `record_ids` list outright (backend guard + schema `min_length=1`).
- `create_qc_batches` had no eligibility validation at all — same
  empty-list gap, plus no check that a record belongs to the lot, is
  `qc_pending`, or isn't already claimed by another active QC task. Now
  enforces the same eligibility check `create_indexing_batch` already had,
  and rejects duplicate record ids across assignments in one request.

## [0.3.5] - 2026-07-20

### Added
- Two more indexer skip reasons, `Lapsed` and `Illegible`, alongside the
  existing `Withdrawn`/`Ineligible`/`Excluded` — same terminal-status
  treatment throughout (never blocks batch completion, no data submitted).

## [0.3.4] - 2026-07-20

### Fixed
- Batch assignment no longer reassigns records that are already sitting in
  another batch. `create_indexing_batch` accepted client-supplied record
  ids with no eligibility check beyond cabinet membership; since a record's
  status stays `pending` until its indexer actually starts the task, a
  record already parented to an unstarted batch was still selectable and
  got silently re-parented to a second batch with a competing task,
  orphaning the original assignment. Only records with `status == pending`
  and no existing `batch_id` are now eligible, and the batch-assignment
  picker (`GET /cabinets/{id}/records?status=pending`) excludes
  already-batched records too.

## [0.3.3] - 2026-07-20

### Fixed
- Cabinet image uploads no longer fail with 413 Request Entity Too Large.
  The digitizing portal's nginx server blocks were missing the
  `client_max_body_size` override that lets large scans through the backend
  upload proxy (`POST /cabinets/{id}/upload`) — they fell back to nginx's
  1MB default, well under typical scan sizes.

## [0.3.2] - 2026-07-20

### Added
- `scripts/wipe_project_data.py`: a reviewable, dry-run-by-default admin CLI
  for permanently deleting all records (versions, tasks, lot_records, lots,
  batches, batch_qc_results, matching audit_logs) and S3 images for a single
  project, while leaving the project and its cabinet(s) in place. Requires
  `--confirm` to execute and, without `--yes`, prompts to re-type the
  project name as a final check; always writes a JSON report of every ID
  touched on a real run.

## [0.3.1] - 2026-07-20

### Fixed
- `scripts/bulk_upload.py` now uses the single-endpoint upload flow
  (`POST /cabinets/{id}/upload`) instead of the removed `upload-url`/
  `confirm-upload` pair — the script was broken for every file, not just
  TIFFs. TIFF-to-PDF conversion needs no client-side handling; it already
  happens server-side inside that same endpoint.

## [0.3.0] - 2026-07-20

### Added
- Indexing workspace: an explicit "Complete Batch" action — indexed records stay
  visible and reopenable in My Tasks until the indexer completes the batch
  themselves, replacing the old implicit auto-advance to QA the moment every
  record was indexed/skipped. My Tasks now groups open-batch work into batch
  cards with a per-record detail view instead of a flat list.

### Changed
- TIFF scans are now converted to PDF once, at upload time, instead of being
  re-decoded and re-encoded to PNG at full resolution on every single view —
  the root cause of reported image-loading slowness.
- Cabinet image uploads now stream through the backend instead of going
  browser-direct to MinIO via a presigned URL.

### Fixed
- Cabinet image uploads no longer fail outright in environments where the
  presigned URL's host isn't browser-resolvable; multi-file drag-upload no
  longer hangs on "Uploading X of Y…" forever.
- Customer supervisors assigning QC work no longer see QC agents belonging to
  other customer organizations in the same tenant (also closed a 404-leak
  letting an unrelated org query any project's QC agent list).
- The customer QC screen now renders array/object indexed-data fields (e.g.
  parcel volume/folio pairs) correctly instead of showing "[object Object]" —
  it reuses the same schema-driven form the DE QA screen uses, in a new
  read-only mode.
- My Tasks list views (batch cards, flat list, batch detail) now have proper
  top/right padding instead of running text edge-to-edge under the header.

## [0.2.0] - 2026-07-17

### Added
- Application versioning: a root `VERSION` file, this changelog, `RELEASING.md`
  documenting the release process, `GET /version`, and the running version/commit
  shown in both portals' header.
- Indexing workspace: pressing Enter in a form field now moves focus to the next
  field, in the order fields appear on the form.
- Indexing workspace: Skip and Submit & Complete now auto-advance to the next task
  in the same batch (indexing and QA) instead of returning to the task list, until
  the batch is done.

### Changed
- The indexer's "Disqualify" action is now "Skip", presenting a direct choice of
  **Withdrawn** or **Ineligible** instead of a free-text reason; either becomes the
  record's new status immediately.

### Fixed
- Record images no longer disappear after loading successfully (a stale, revoked
  blob URL could be served back from cache on remount).
- Users are no longer logged out while actively working (the SSO session now stays
  alive during genuine activity instead of idling out silently in the background).

## [0.1.0] - 2026-07-07

Baseline release — the `v0.1.0` git tag was already in place before this changelog
was introduced, so this entry marks the starting point rather than listing granular
history. Future releases should list actual changes under `[Unreleased]` as they
land, moved here at release time.
