"""Help: the customer manuals (services/help_service.py).

The HTML pages themselves are served without sign-in under
/api/help/static (mounted in main.py): they hold no customer data. The
endpoints here list what the signed-in user may read and produce the PDF,
which is stamped with the user's organisation and audited.
"""
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.organization import Organization
from app.models.user import Portal
from app.services import help_service

router = APIRouter(prefix="/api/help", tags=["help"])


class ManualOut(BaseModel):
    slug: str
    title: str
    kind: str
    html_url: str
    pdf_url: str


class ManualList(BaseModel):
    version: str
    manuals: list[ManualOut]


def _customer_only(user) -> None:
    if user.portal != Portal.customer:
        raise HTTPException(status_code=403, detail="Manuals are available in the customer portal")


def _catalogue():
    try:
        return help_service.catalogue()
    except help_service.ManualsNotBuilt:
        raise HTTPException(status_code=503, detail="The manuals haven't been built on this server")


@router.get("/manuals", response_model=ManualList)
async def list_manuals(current_user=Depends(get_current_user)):
    _customer_only(current_user)
    version, _ = _catalogue()
    return ManualList(
        version=version,
        manuals=[
            ManualOut(
                slug=m.slug, title=m.title, kind=m.kind,
                html_url=f"/api/help/static/{m.slug}.html",
                pdf_url=f"/api/help/manuals/{m.slug}.pdf",
            )
            for m in help_service.manuals_for(current_user.role.value)
        ],
    )


@router.get("/manuals/{slug}.pdf")
async def download_manual_pdf(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """The manual as a PDF prepared for the user's organisation (named on the
    cover and in every page footer). Each download is audited."""
    _customer_only(current_user)
    version, _ = _catalogue()
    manual = help_service.get_manual(slug, current_user.role.value)
    if manual is None:
        raise HTTPException(status_code=404, detail="Manual not found")
    org = await db.get(Organization, current_user.organization_id) if current_user.organization_id else None
    if org is None or org.tenant_id != current_user._tenant_id:
        raise HTTPException(status_code=403, detail="Your account isn't linked to an organisation")

    issued_on = datetime.now(timezone.utc).date()
    pdf = await run_in_threadpool(help_service.render_pdf, manual.slug, org.name, issued_on, version)
    await help_service.record_download(
        db, tenant_id=current_user._tenant_id, org_id=org.id, user_id=current_user.id,
        manual=manual, version=version,
    )
    await db.commit()

    org_part = re.sub(r"[^A-Za-z0-9]+", "-", org.name).strip("-") or "organisation"
    filename = f"DocMate-{manual.title.replace(' ', '-')}-v{version}-{org_part}.pdf"
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
