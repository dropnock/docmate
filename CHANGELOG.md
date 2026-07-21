# Changelog

All notable changes to DocMate are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/).

See `RELEASING.md` for how to cut a release.

## [Unreleased]

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
