import argparse
import json
import os
from pathlib import Path

import httpx
import pytest

from scripts.bulk_upload import (
    discover_files,
    parse_args,
    resolve_client_secret,
    upload_one,
    validate_api_url,
    write_report,
    AuthSession,
    FileResult,
)


def test_discover_files_filters_by_extension_and_skips_hidden(tmp_path):
    (tmp_path / "a.pdf").write_text("x")
    (tmp_path / "b.txt").write_text("x")
    (tmp_path / ".hidden.pdf").write_text("x")
    hidden_dir = tmp_path / ".git"
    hidden_dir.mkdir()
    (hidden_dir / "c.pdf").write_text("x")

    files = discover_files(tmp_path, recursive=True, extensions={"pdf"})
    assert [f.name for f in files] == ["a.pdf"]


def test_discover_files_recursive_vs_flat(tmp_path):
    (tmp_path / "top.pdf").write_text("x")
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "nested.pdf").write_text("x")

    flat = discover_files(tmp_path, recursive=False, extensions={"pdf"})
    assert [f.name for f in flat] == ["top.pdf"]

    recursive = discover_files(tmp_path, recursive=True, extensions={"pdf"})
    assert sorted(f.name for f in recursive) == ["nested.pdf", "top.pdf"]


def test_resolve_client_secret_precedence_file_over_env(tmp_path, monkeypatch):
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("from-file\n")
    monkeypatch.setenv("DOCMATE_CLI_CLIENT_SECRET", "from-env")

    args = argparse.Namespace(client_secret_file=secret_file, client_secret=None)
    assert resolve_client_secret(args) == "from-file"


def test_resolve_client_secret_env_over_inline(monkeypatch):
    monkeypatch.setenv("DOCMATE_CLI_CLIENT_SECRET", "from-env")
    args = argparse.Namespace(client_secret_file=None, client_secret="from-inline")
    assert resolve_client_secret(args) == "from-env"


def test_resolve_client_secret_falls_back_to_inline(monkeypatch, capsys):
    monkeypatch.delenv("DOCMATE_CLI_CLIENT_SECRET", raising=False)
    args = argparse.Namespace(client_secret_file=None, client_secret="from-inline")
    assert resolve_client_secret(args) == "from-inline"
    assert "WARNING" in capsys.readouterr().err


def test_resolve_client_secret_none_provided_exits(monkeypatch):
    monkeypatch.delenv("DOCMATE_CLI_CLIENT_SECRET", raising=False)
    args = argparse.Namespace(client_secret_file=None, client_secret=None)
    with pytest.raises(SystemExit):
        resolve_client_secret(args)


def test_validate_api_url_allows_localhost_http():
    validate_api_url("http://localhost:8000", allow_insecure_http=False)  # no raise


def test_validate_api_url_blocks_remote_http_by_default():
    with pytest.raises(SystemExit):
        validate_api_url("http://prod.example.com", allow_insecure_http=False)


def test_validate_api_url_allows_remote_http_with_override(capsys):
    validate_api_url("http://prod.example.com", allow_insecure_http=True)  # no raise
    assert "WARNING" in capsys.readouterr().err


def test_write_report_csv(tmp_path):
    results = [FileResult(filename="a.pdf", status="success", record_id=1, source_identifier="a")]
    path = tmp_path / "report.csv"
    write_report(path, results)
    content = path.read_text()
    assert "a.pdf" in content and "success" in content


def test_write_report_json(tmp_path):
    results = [FileResult(filename="a.pdf", status="failed", error="boom")]
    path = tmp_path / "report.json"
    write_report(path, results)
    data = json.loads(path.read_text())
    assert data[0]["filename"] == "a.pdf"
    assert data[0]["status"] == "failed"
    assert data[0]["error"] == "boom"


def test_write_report_rejects_unknown_extension(tmp_path):
    with pytest.raises(SystemExit):
        write_report(tmp_path / "report.txt", [])


def test_upload_one_dry_run_skips_network(tmp_path):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-fake")
    result = upload_one(f, cabinet_id=1, api_url="https://example.invalid", session=None, dry_run=True)
    assert result.status == "skipped"


def test_upload_one_uses_basename_even_when_nested(tmp_path):
    """Defensive check: only the basename is ever sent as `filename`, even
    though discover_files may return a nested path under --recursive — the
    server has no path sanitization on the filename query param."""
    nested = tmp_path / "sub" / "dir"
    nested.mkdir(parents=True)
    f = nested / "scan.pdf"
    f.write_bytes(b"%PDF-fake")
    result = upload_one(f, cabinet_id=1, api_url="https://example.invalid", session=None, dry_run=True)
    assert result.filename == "scan.pdf"
    assert "/" not in result.filename


def test_upload_one_full_flow_success(tmp_path):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-fake-bytes")

    upload_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/cabinets/1/upload":
            upload_calls.append(request)
            return httpx.Response(200, json={"id": 99, "source_identifier": "scan"})
        raise AssertionError(f"unexpected request to {request.url}")

    api_transport = httpx.MockTransport(handler)

    class FakeSession:
        def request(self, method, url, **kwargs):
            with httpx.Client(transport=api_transport) as client:
                return client.request(method, url, **kwargs)

    result = upload_one(
        f, cabinet_id=1, api_url="https://example.invalid", session=FakeSession(), dry_run=False
    )

    assert result.status == "success"
    assert result.record_id == 99
    assert result.source_identifier == "scan"
    assert len(upload_calls) == 1


def test_upload_one_sends_tiff_as_is_for_server_side_conversion(tmp_path):
    """TIFF-to-PDF conversion happens server-side (cabinet_service.
    _convert_tiff_if_needed) — the script just uploads the raw .tiff bytes
    with an image/tiff content type, same as any other extension."""
    f = tmp_path / "scan.tiff"
    f.write_bytes(b"fake-tiff-bytes")

    upload_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/cabinets/1/upload"
        upload_calls.append(request)
        return httpx.Response(200, json={"id": 100, "source_identifier": "scan"})

    api_transport = httpx.MockTransport(handler)

    class FakeSession:
        def request(self, method, url, **kwargs):
            with httpx.Client(transport=api_transport) as client:
                return client.request(method, url, **kwargs)

    result = upload_one(
        f, cabinet_id=1, api_url="https://example.invalid", session=FakeSession(), dry_run=False
    )

    assert result.status == "success"
    assert len(upload_calls) == 1
    assert b"image/tiff" in upload_calls[0].content


def test_upload_one_failure_is_caught_not_raised(tmp_path, monkeypatch):
    import scripts.bulk_upload as bulk_upload_module

    monkeypatch.setattr(bulk_upload_module.time, "sleep", lambda *_: None)  # skip retry backoff delay

    f = tmp_path / "scan.pdf"
    f.write_bytes(b"data")

    class FailingSession:
        def request(self, method, url, **kwargs):
            raise httpx.ConnectError("boom", request=httpx.Request(method, url))

    result = upload_one(
        f, cabinet_id=1, api_url="https://example.invalid", session=FailingSession(), dry_run=False
    )
    assert result.status == "failed"
    assert "boom" in result.error
