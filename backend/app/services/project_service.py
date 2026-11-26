"""Project details and the quality settings set up with them, audited.

The AQLs set here are the project's settings for lots and inspections from
now on. Each customer sample and outgoing inspection keeps its own copy of
the plan it was started with (Lot.class_plan / LotInspection.class_plan),
so changing them mid-project never changes an earlier verdict.
"""
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aql import AQLConfig
from app.models.audit_log import AuditAction, AuditEntityType
from app.models.project import Project
from app.services import aql_service, audit_service

PROJECT_FIELDS = ("name", "description", "proposed_end_date")
AQL_FIELDS = ("critical_aql", "major_aql", "minor_aql")


def _plain(v: Any) -> Any:
    return v.isoformat() if isinstance(v, date) else v


async def log_created(
    db: AsyncSession, *, project: Project, config: AQLConfig, user_id: int, tenant_id: int,
) -> None:
    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.project,
        entity_id=project.id,
        action=AuditAction.created,
        performed_by=user_id,
        new_value={
            "name": project.name,
            "description": project.description,
            "customer_org_id": project.customer_org_id,
            "proposed_end_date": _plain(project.proposed_end_date),
            "critical_aql": config.critical_aql,
            "major_aql": config.normal_aql,
            "minor_aql": config.minor_aql,
        },
    )


async def update_project(
    db: AsyncSession,
    *,
    project: Project,
    fields: dict[str, Any],
    aql_updates: dict[str, Any],
    user_id: int,
    tenant_id: int,
) -> Project:
    """Apply an edit: one settings_changed event with the project fields that
    changed (old and new), and the AQL changes as aql_service.update_config's
    own event."""
    old_value: dict[str, Any] = {}
    new_value: dict[str, Any] = {}
    for field in PROJECT_FIELDS:
        if field not in fields:
            continue
        value = fields[field]
        if getattr(project, field) == value:
            continue
        old_value[field] = _plain(getattr(project, field))
        new_value[field] = _plain(value)
        setattr(project, field, value)
    if new_value:
        await audit_service.write_event(
            db,
            tenant_id=tenant_id,
            entity_type=AuditEntityType.project,
            entity_id=project.id,
            action=AuditAction.settings_changed,
            performed_by=user_id,
            old_value=old_value,
            new_value=new_value,
        )
    if aql_updates:
        await aql_service.update_config(
            db, project_id=project.id, updates=dict(aql_updates), user_id=user_id, tenant_id=tenant_id,
        )
    return project
