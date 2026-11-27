"""In-app manuals: manuals/build.py output, GET /api/help/*, and the
organisation-stamped PDF (services/help_service.py)."""
import importlib.util
import io
import shutil
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.main import app
from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.user import Portal, User, UserRole
from app.services import help_service
from tests.conftest import token

REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")


def _load_build():
    spec = importlib.util.spec_from_file_location("manuals_build", REPO / "manuals" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["manuals_build"] = mod
    spec.loader.exec_module(mod)
    return mod


build = _load_build()


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("manuals")
    build.build(out, "9.9.9")
    return out


@pytest.fixture(autouse=True)
def use_built(built, monkeypatch, tmp_path):
    """Point the service and the /api/help/static mount at the test build."""
    monkeypatch.setattr(settings, "manuals_dir", str(built))
    monkeypatch.setattr(help_service, "_cache_dir", lambda: tmp_path)
    mount = next(r for r in app.routes if getattr(r, "name", None) == "help-static")
    monkeypatch.setattr(mount.app, "directory", built / "web")
    monkeypatch.setattr(mount.app, "all_directories", [built / "web"])


def auth(user):
    return {"Authorization": f"Bearer {token(user)}", "X-Portal": user.portal.value}


@pytest.fixture
async def cust_supervisor(db: AsyncSession, seed):
    u = User(
        tenant_id=seed["tenant"].id, organization_id=seed["cust_org"].id, email="csup@test.com",
        keycloak_sub="sub-csup", full_name="Cust Supervisor",
        role=UserRole.customer_supervisor, portal=Portal.customer, is_active=True,
    )
    db.add(u)
    await db.commit()
    return u


class TestBuild:
    def test_writes_every_manual(self, built):
        cat = (built / "manuals.json").read_text()
        for slug in ("supervisor-manual", "reviewer-manual", "supervisor-workbook", "reviewer-workbook"):
            assert (built / "web" / f"{slug}.html").is_file()
            assert (built / "print" / f"{slug}.html").is_file()
            assert slug in cat
        assert (built / "web" / "assets" / "brand.css").is_file()

    def test_brand_colours_come_from_brand_yaml(self, built):
        css = (built / "web" / "assets" / "brand.css").read_text()
        assert "--primary: #1E40AF;" in css
        assert "font-family: 'Inter'" in css

    def test_page_has_version_toc_and_no_duplicate_title(self, built):
        page = (built / "web" / "supervisor-manual.html").read_text()
        assert "v9.9.9" in page
        assert 'href="#7-lot-settings-and-the-aql-configuration"' in page
        assert page.count("<h1") == 1

    def test_print_page_has_stamp_placeholders(self, built):
        page = (built / "print" / "reviewer-manual.html").read_text()
        assert build.PREPARED_FOR in page and build.ISSUED_ON in page

    def test_broken_anchor_fails_the_build(self, tmp_path):
        md = tmp_path / "x.md"
        md.write_text("x")
        with pytest.raises(build.BuildError, match="missing anchor #nowhere"):
            build.check('<h2 id="here">H</h2><a href="#nowhere">x</a>', md)

    def test_missing_image_fails_the_build(self, tmp_path):
        md = tmp_path / "x.md"
        md.write_text("x")
        with pytest.raises(build.BuildError, match="missing image images/nope.png"):
            build.check('<img src="images/nope.png">', md)


class TestList:
    async def test_reviewer_sees_reviewer_manuals(self, client, seed):
        r = await client.get("/api/help/manuals", headers=auth(seed["qc_agent"]))
        assert r.status_code == 200
        body = r.json()
        assert body["version"] == "9.9.9"
        assert [m["slug"] for m in body["manuals"]] == ["reviewer-manual", "reviewer-workbook"]
        assert body["manuals"][0]["html_url"] == "/api/help/static/reviewer-manual.html"

    async def test_supervisor_sees_all_four(self, client, cust_supervisor):
        r = await client.get("/api/help/manuals", headers=auth(cust_supervisor))
        assert len(r.json()["manuals"]) == 4

    async def test_digitizing_portal_is_refused(self, client, seed):
        r = await client.get("/api/help/manuals", headers=auth(seed["supervisor"]))
        assert r.status_code == 403

    async def test_not_built_is_503(self, client, seed, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "manuals_dir", str(tmp_path / "none"))
        r = await client.get("/api/help/manuals", headers=auth(seed["qc_agent"]))
        assert r.status_code == 503


class TestStatic:
    async def test_page_and_images_served_without_sign_in(self, client):
        r = await client.get("/api/help/static/reviewer-manual.html")
        assert r.status_code == 200 and "Reviewer User Manual" in r.text
        r = await client.get("/api/help/static/images/reviewer/10b_choose_what_is_wrong.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"


class TestPdf:
    async def test_pdf_is_stamped_with_org_and_audited(self, client, db: AsyncSession, seed):
        pypdf = pytest.importorskip("pypdf")
        pytest.importorskip("weasyprint")
        r = await client.get("/api/help/manuals/reviewer-workbook.pdf", headers=auth(seed["qc_agent"]))
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/pdf"
        assert "Cust-Org" in r.headers["content-disposition"]
        pages = pypdf.PdfReader(io.BytesIO(r.content)).pages
        assert len(pages) > 3
        for page in (pages[0], pages[len(pages) // 2], pages[-1]):
            assert "Prepared for Cust Org" in page.extract_text()

        event = (await db.execute(
            select(AuditLog).where(AuditLog.action == AuditAction.manual_downloaded)
        )).scalar_one()
        assert event.entity_type == AuditEntityType.organization
        assert event.entity_id == seed["cust_org"].id
        assert event.performed_by == seed["qc_agent"].id
        assert event.new_value["manual"] == "reviewer-workbook"
        assert event.new_value["version"] == "9.9.9"

    async def test_org_name_is_escaped(self, client, db: AsyncSession, seed):
        pytest.importorskip("weasyprint")
        pypdf = pytest.importorskip("pypdf")
        seed["cust_org"].name = "Smith & <Jones>"
        await db.commit()
        r = await client.get("/api/help/manuals/reviewer-workbook.pdf", headers=auth(seed["qc_agent"]))
        assert r.status_code == 200
        text = pypdf.PdfReader(io.BytesIO(r.content)).pages[0].extract_text()
        assert "Smith & <Jones>" in text

    async def test_reviewer_cannot_get_supervisor_manual(self, client, seed):
        r = await client.get("/api/help/manuals/supervisor-manual.pdf", headers=auth(seed["qc_agent"]))
        assert r.status_code == 404

    async def test_unknown_manual_is_404(self, client, seed):
        r = await client.get("/api/help/manuals/nope.pdf", headers=auth(seed["qc_agent"]))
        assert r.status_code == 404
