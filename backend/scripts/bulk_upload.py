#!/usr/bin/env python3
"""
Bulk-upload files from a local directory into a DocMate cabinet.

Authenticates as the docmate-cli Keycloak service account (see
../provision_cli_user.py) via the OAuth2 client_credentials grant — no
interactive login, no human credentials involved.

Example:
    python scripts/bulk_upload.py \\
        --api-url https://www.docmate.local \\
        --keycloak-url https://auth.docmate.local \\
        --cabinet-id 42 \\
        --directory /path/to/scans \\
        --ca-bundle ../nginx/certs/dev.crt

Run with --help for the full flag list.
"""
import argparse
import csv
import json
import mimetypes
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

DEFAULT_EXTENSIONS = "pdf,jpg,jpeg,png,tif,tiff"
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0


@dataclass
class FileResult:
    filename: str
    status: str  # "success" | "failed" | "skipped"
    record_id: int | None = None
    source_identifier: str | None = None
    error: str | None = None


class AuthSession:
    """Bearer-token wrapper with one-shot refresh on 401.

    Thread-safe: token refresh is serialized via a lock so concurrent
    workers never race each other into fetching two tokens for one 401.
    """

    def __init__(self, http_client: httpx.Client, keycloak_url: str, realm: str, client_id: str, client_secret: str):
        self._http_client = http_client
        self._keycloak_url = keycloak_url
        self._realm = realm
        self._client_id = client_id
        self._client_secret = client_secret
        self._lock = threading.Lock()
        self._token = self._fetch_token()

    def _fetch_token(self) -> str:
        token_url = f"{self._keycloak_url.rstrip('/')}/realms/{self._realm}/protocol/openid-connect/token"
        resp = self._http_client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        with self._lock:
            token = self._token
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        resp = self._http_client.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401:
            with self._lock:
                self._token = self._fetch_token()
                token = self._token
            headers["Authorization"] = f"Bearer {token}"
            resp = self._http_client.request(method, url, headers=headers, **kwargs)
        return resp


def with_retries(fn, *, max_attempts: int = RETRY_MAX_ATTEMPTS, base_delay: float = RETRY_BASE_DELAY):
    """Retry fn() on connection errors or 5xx, with exponential backoff.
    Does not retry 4xx — those aren't transient. Raises the last error if
    all attempts are exhausted."""
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except httpx.TransportError as exc:
            last_exc = exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise
            last_exc = exc
        if attempt < max_attempts - 1:
            time.sleep(base_delay * (2**attempt))
    raise last_exc


def resolve_client_secret(args: argparse.Namespace) -> str:
    if args.client_secret_file:
        secret = args.client_secret_file.read_text().strip()
        if not secret:
            print("ERROR: --client-secret-file is empty", file=sys.stderr)
            sys.exit(2)
        return secret
    env_secret = os.environ.get("DOCMATE_CLI_CLIENT_SECRET")
    if env_secret:
        return env_secret.strip()
    if args.client_secret:
        print(
            "WARNING: passing --client-secret on the command line exposes it in shell "
            "history and process listings (ps); prefer --client-secret-file or "
            "DOCMATE_CLI_CLIENT_SECRET.",
            file=sys.stderr,
        )
        return args.client_secret.strip()
    print(
        "ERROR: no client secret provided (use --client-secret-file, "
        "DOCMATE_CLI_CLIENT_SECRET, or --client-secret)",
        file=sys.stderr,
    )
    sys.exit(2)


def validate_api_url(api_url: str, allow_insecure_http: bool) -> None:
    parsed = urlparse(api_url)
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
        if not allow_insecure_http:
            print(
                f"ERROR: --api-url uses plain http:// against non-localhost host "
                f"'{parsed.hostname}'. This would send the bearer token unencrypted. "
                f"Pass --allow-insecure-http to override if you really intend this.",
                file=sys.stderr,
            )
            sys.exit(2)
        print(
            f"WARNING: proceeding with plain http:// to '{parsed.hostname}' "
            f"(--allow-insecure-http set) — bearer token is sent unencrypted.",
            file=sys.stderr,
        )


def discover_files(directory: Path, recursive: bool, extensions: set[str]) -> list[Path]:
    pattern_iter = directory.rglob("*") if recursive else directory.glob("*")
    files = [
        p
        for p in pattern_iter
        if p.is_file()
        and not any(part.startswith(".") for part in p.relative_to(directory).parts)
        and p.suffix.lstrip(".").lower() in extensions
    ]
    return sorted(files)


def upload_one(
    file: Path,
    cabinet_id: int,
    api_url: str,
    session: AuthSession,
    dry_run: bool,
) -> FileResult:
    filename = file.name  # basename only — see note in discover_files call site

    if dry_run:
        return FileResult(filename=filename, status="skipped")

    try:
        content_type, _ = mimetypes.guess_type(filename)
        content_type = content_type or "application/octet-stream"

        def _upload():
            # Single streamed multipart upload — the old upload-url/PUT-to-S3/
            # confirm-upload dance is gone (see POST /cabinets/{id}/upload,
            # which now receives the file on the backend's own origin and
            # writes it to S3 server-side). TIFF scans are converted to a
            # multi-page PDF server-side at this same step
            # (cabinet_service._convert_tiff_if_needed) — this script sends
            # the raw .tif/.tiff bytes exactly like any other extension and
            # does no client-side conversion of its own.
            with file.open("rb") as fh:
                resp = session.request(
                    "POST",
                    f"{api_url}/api/cabinets/{cabinet_id}/upload",
                    files={"file": (filename, fh, content_type)},
                )
                resp.raise_for_status()
                return resp

        upload_resp = with_retries(_upload)
        upload_data = upload_resp.json()

        return FileResult(
            filename=filename,
            status="success",
            record_id=upload_data.get("id"),
            source_identifier=upload_data.get("source_identifier"),
        )
    except Exception as exc:
        return FileResult(filename=filename, status="failed", error=str(exc))


def write_report(path: Path, results: list[FileResult]) -> None:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["filename", "status", "record_id", "source_identifier", "error"])
            writer.writeheader()
            for r in results:
                writer.writerow(asdict(r))
    elif suffix == ".json":
        with path.open("w") as fh:
            json.dump([asdict(r) for r in results], fh, indent=2, default=str)
    else:
        sys.exit("ERROR: --report path must end in .csv or .json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bulk-upload files from a directory into a DocMate cabinet.",
        epilog=(
            "Client secret can be supplied via --client-secret-file (recommended), "
            "the DOCMATE_CLI_CLIENT_SECRET env var, or (least preferred) --client-secret."
        ),
    )
    parser.add_argument("--api-url", required=True, help="Digitizing portal base URL, e.g. https://www.docmate.local")
    parser.add_argument("--keycloak-url", required=True, help="Keycloak base URL, e.g. https://auth.docmate.local")
    parser.add_argument("--realm", default="doc")
    parser.add_argument("--client-id", default="docmate-cli")
    parser.add_argument("--client-secret-file", type=Path, default=None)
    parser.add_argument("--client-secret", default=None, help="Least preferred — see epilog.")
    parser.add_argument("--cabinet-id", type=int, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--extensions", default=DEFAULT_EXTENSIONS, help=f"Comma-separated, default: {DEFAULT_EXTENSIONS}")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path, default=None, help="Write results manifest as .csv or .json")
    parser.add_argument("--ca-bundle", type=Path, default=None, help="Custom CA bundle for TLS verification")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification")
    parser.add_argument(
        "--allow-insecure-http", action="store_true", help="Permit plain http:// against a non-localhost --api-url"
    )

    args = parser.parse_args(argv)

    if not args.directory.is_dir():
        parser.error(f"--directory does not exist or is not a directory: {args.directory}")
    if args.report and args.report.suffix.lower() not in (".csv", ".json"):
        parser.error("--report path must end in .csv or .json")

    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    validate_api_url(args.api_url, args.allow_insecure_http)
    client_secret = resolve_client_secret(args)

    if args.ca_bundle:
        verify: bool | str = str(args.ca_bundle)
    elif args.insecure:
        print(
            "WARNING: TLS certificate verification is DISABLED (--insecure). "
            "Do not use this against a production endpoint.",
            file=sys.stderr,
        )
        verify = False
    else:
        verify = True

    extensions = {e.strip().lower().lstrip(".") for e in args.extensions.split(",") if e.strip()}

    with httpx.Client(verify=verify, timeout=30.0) as http_client:
        try:
            session = AuthSession(http_client, args.keycloak_url, args.realm, args.client_id, client_secret)
        except httpx.HTTPError as exc:
            print(f"ERROR: failed to authenticate against Keycloak: {exc}", file=sys.stderr)
            sys.exit(2)

        files = discover_files(args.directory, args.recursive, extensions)
        if not files:
            print("No matching files found.")
            return 0

        print(f"Found {len(files)} file(s) to upload to cabinet {args.cabinet_id}" + (" (dry run)" if args.dry_run else ""))

        results_by_file: dict[Path, FileResult] = {}
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {
                pool.submit(upload_one, f, args.cabinet_id, args.api_url, session, args.dry_run): f
                for f in files
            }
            for future in as_completed(futures):
                f = futures[future]
                result = future.result()
                results_by_file[f] = result
                extra = f" -> record_id={result.record_id}" if result.record_id else ""
                extra += f" ({result.error})" if result.error else ""
                print(f"[{result.status.upper():7}] {result.filename}{extra}")

    results = [results_by_file[f] for f in files]  # deterministic order for the report
    succeeded = sum(1 for r in results if r.status == "success")
    failed = [r for r in results if r.status == "failed"]
    skipped = sum(1 for r in results if r.status == "skipped")

    print(f"\nSummary: {succeeded} succeeded, {len(failed)} failed, {skipped} skipped (of {len(results)} total)")
    if failed:
        print("Failed files:")
        for r in failed:
            print(f"  - {r.filename}: {r.error}")

    if args.report:
        write_report(args.report, results)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
