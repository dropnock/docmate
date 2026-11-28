"""In-app manuals (Help).

The manuals are built from manuals/ by manuals/build.py — at image build
time in Docker, by hand in local dev — into settings.manuals_dir:

    web/<slug>.html, web/assets/, web/images/   served as-is under /api/help/static
    print/<slug>.html                           print layout, rendered to PDF here
    manuals.json                                the catalogue (slug, title, kind, roles)

PDFs are made on demand with WeasyPrint and carry the downloading customer
organisation's name on the cover and in every page footer, so a copy that
gets passed around can be traced back to the organisation it was issued to.
Each download is audited (AuditEntityType.organization / manual_downloaded).
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.audit_log import AuditAction, AuditEntityType
from app.services import audit_service

logger = logging.getLogger(__name__)

# Must match manuals/build.py.
PREPARED_FOR = "__DOCMATE_PREPARED_FOR__"
ISSUED_ON = "__DOCMATE_ISSUED_ON__"

_REPO_DIST = Path(__file__).resolve().parents[3] / "manuals" / "dist"
_IMAGE_DIST = Path("/app/manuals_dist")


class ManualsNotBuilt(Exception):
    pass


@dataclass(frozen=True)
class Manual:
    slug: str
    title: str
    kind: str
    roles: tuple[str, ...]


def manuals_dir() -> Path:
    if settings.manuals_dir:
        return Path(settings.manuals_dir)
    return _IMAGE_DIST if _IMAGE_DIST.is_dir() else _REPO_DIST


@lru_cache(maxsize=4)
def _load_catalogue(path: str, mtime: float) -> tuple[str, tuple[Manual, ...]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    manuals = tuple(
        Manual(slug=m["slug"], title=m["title"], kind=m["kind"], roles=tuple(m["roles"]))
        for m in data["manuals"]
    )
    return data["version"], manuals


def catalogue() -> tuple[str, tuple[Manual, ...]]:
    """(manuals version, every manual). Raises ManualsNotBuilt if the build
    output is missing."""
    path = manuals_dir() / "manuals.json"
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        raise ManualsNotBuilt(f"{path} not found; run manuals/build.py") from None
    return _load_catalogue(str(path), mtime)


def manuals_for(role: str) -> list[Manual]:
    _, manuals = catalogue()
    return [m for m in manuals if role in m.roles]


def get_manual(slug: str, role: str) -> Manual | None:
    return next((m for m in manuals_for(role) if m.slug == slug), None)


def _cache_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "docmate-manual-pdfs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def render_pdf(slug: str, org_name: str, issued_on: date, version: str) -> bytes:
    """The manual as a PDF prepared for `org_name`. Blocking (WeasyPrint is
    CPU-bound): call it in a worker thread. Cached on disk per manual,
    organisation name, issue date and version."""
    key = hashlib.sha256(f"{slug}\0{org_name}\0{issued_on.isoformat()}\0{version}".encode()).hexdigest()[:32]
    cached = _cache_dir() / f"{slug}-{key}.pdf"
    if cached.is_file():
        return cached.read_bytes()

    from weasyprint import HTML  # heavy import; only needed here

    root = manuals_dir()
    source = (root / "print" / f"{slug}.html").read_text(encoding="utf-8")
    source = source.replace(PREPARED_FOR, html.escape(org_name)).replace(
        ISSUED_ON, f"{issued_on.day} {issued_on:%B %Y}"
    )
    pdf = HTML(string=source, base_url=str(root / "web") + "/").write_pdf(
        # Embedded metadata travels with the file too.
        custom_metadata=True,
    )
    tmp = cached.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_bytes(pdf)
    os.replace(tmp, cached)
    return pdf


async def record_download(
    db: AsyncSession, *, tenant_id: int, org_id: int, user_id: int, manual: Manual, version: str,
) -> None:
    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.organization, entity_id=org_id,
        action=AuditAction.manual_downloaded, performed_by=user_id,
        new_value={"manual": manual.slug, "title": manual.title, "version": version},
    )
