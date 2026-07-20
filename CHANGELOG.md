# Changelog

All notable changes to DocMate are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/).

See `RELEASING.md` for how to cut a release.

## [Unreleased]

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
