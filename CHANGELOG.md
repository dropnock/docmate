# Changelog

All notable changes to DocMate are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/).

See `RELEASING.md` for how to cut a release.

## [Unreleased]

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
