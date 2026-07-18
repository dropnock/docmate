# Cutting a release

DocMate uses manual [SemVer](https://semver.org/) versioning. The `VERSION` file at
the repo root is the single source of truth; there is no CI/CD automation for this
(see `CHANGELOG.md`/`VERSION` — build-time stamping details in `docker-compose.yml`
and the `backend`/`frontend` Dockerfiles).

1. Update `CHANGELOG.md`: move the `[Unreleased]` entries under a new
   `## [X.Y.Z] - YYYY-MM-DD` heading, leaving `[Unreleased]` empty above it.
2. Bump the `VERSION` file to `X.Y.Z`.
3. Commit: `git commit -am "chore(release): vX.Y.Z"`.
4. Tag: `git tag -a vX.Y.Z -m "vX.Y.Z"`.
5. Build with the version stamped into the images (falls back to `dev`/`unknown`
   if you skip this, which is fine for everyday local dev — only a real release
   needs this step):
   ```
   export APP_VERSION=$(cat VERSION) GIT_COMMIT=$(git rev-parse --short HEAD)
   docker-compose build
   ```
6. Push: `git push && git push --tags`.
7. Deploy however this environment is currently deployed — not covered here, since
   this repo has no deploy pipeline to hook into yet.

## Verifying what's running

- `curl <backend>/version` → `{"version": "X.Y.Z", "git_commit": "abc1234"}`.
- `curl <backend>/health` includes the same two fields.
- Both portals show the version in the header (top left, next to "DocMate").
