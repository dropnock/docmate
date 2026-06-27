"""initial schema

Revision ID: 4lequxy65bsv
Revises:
Create Date: 2026-06-27 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "4lequxy65bsv"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Enums: create explicitly with IF NOT EXISTS ---
    _enum_defs = [
        ("orgtype", ["digitizing_entity", "customer"]),
        ("userrole", ["admin", "de_supervisor", "de_indexer", "de_qa_agent",
                      "customer_supervisor", "customer_qc_agent"]),
        ("portal", ["digitizing", "customer"]),
        ("s3bucketstatus", ["provisioning", "ready", "error"]),
        ("batchstatus", ["draft", "submitted", "indexing", "qa_review",
                         "customer_qc", "passed", "rejected"]),
        ("recordstatus", ["pending", "indexing", "indexed",
                          "qa_pending", "qa_passed", "qa_failed",
                          "qc_pending", "qc_passed", "qc_failed"]),
        ("versionreason", ["initial_indexing", "rework_after_qa",
                           "rework_after_customer_rejection"]),
        ("tasktype", ["indexing", "qa", "qc"]),
        ("taskstatus", ["pending", "in_progress", "completed", "failed", "stale"]),
        ("aqlstatus", ["normal", "tightened", "reduced"]),
        ("auditentitytype", ["record", "task", "batch", "project", "user"]),
        ("auditaction", ["created", "status_changed", "locked", "unlocked", "lock_expired",
                         "assigned", "reassigned", "sampled", "indexing_submitted",
                         "version_created", "qa_passed", "qa_failed", "qc_passed",
                         "qc_rejected", "batch_escalated", "stale_flagged", "deactivated"]),
    ]
    for name, values in _enum_defs:
        sa.Enum(*values, name=name).create(op.get_bind(), checkfirst=True)

    # --- Enum column refs (create_type=False — types already exist above) ---
    orgtype_enum = sa.Enum("digitizing_entity", "customer", name="orgtype", create_type=False)
    userrole_enum = sa.Enum(
        "admin", "de_supervisor", "de_indexer", "de_qa_agent",
        "customer_supervisor", "customer_qc_agent", name="userrole", create_type=False,
    )
    portal_enum = sa.Enum("digitizing", "customer", name="portal", create_type=False)
    s3status_enum = sa.Enum("provisioning", "ready", "error", name="s3bucketstatus", create_type=False)
    batchstatus_enum = sa.Enum(
        "draft", "submitted", "indexing", "qa_review", "customer_qc", "passed", "rejected",
        name="batchstatus", create_type=False,
    )
    recordstatus_enum = sa.Enum(
        "pending", "indexing", "indexed",
        "qa_pending", "qa_passed", "qa_failed",
        "qc_pending", "qc_passed", "qc_failed",
        name="recordstatus", create_type=False,
    )
    versionreason_enum = sa.Enum(
        "initial_indexing", "rework_after_qa", "rework_after_customer_rejection",
        name="versionreason", create_type=False,
    )
    tasktype_enum = sa.Enum("indexing", "qa", "qc", name="tasktype", create_type=False)
    taskstatus_enum = sa.Enum(
        "pending", "in_progress", "completed", "failed", "stale", name="taskstatus", create_type=False,
    )
    aqlstatus_enum = sa.Enum("normal", "tightened", "reduced", name="aqlstatus", create_type=False)
    auditentity_enum = sa.Enum(
        "record", "task", "batch", "project", "user", name="auditentitytype", create_type=False,
    )
    auditaction_enum = sa.Enum(
        "created", "status_changed", "locked", "unlocked", "lock_expired",
        "assigned", "reassigned", "sampled", "indexing_submitted", "version_created",
        "qa_passed", "qa_failed", "qc_passed", "qc_rejected",
        "batch_escalated", "stale_flagged", "deactivated",
        name="auditaction", create_type=False,
    )

    # --- Tables (skip any that already exist from a partial prior run) ---
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())

    if "tenants" not in existing_tables:
        op.create_table(
            "tenants",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("slug", sa.String(100), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

        op.create_table(
            "organizations",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=False, index=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("type", orgtype_enum, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

        op.create_table(
            "users",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=False, index=True),
            sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=True),
            sa.Column("email", sa.String(320), nullable=False, index=True),
            sa.Column("hashed_password", sa.String(255), nullable=False),
            sa.Column("full_name", sa.String(255), nullable=False),
            sa.Column("role", userrole_enum, nullable=False),
            sa.Column("portal", portal_enum, nullable=False),
            sa.Column("is_active", sa.Boolean, default=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

        op.create_table(
            "projects",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=False, index=True),
            sa.Column("digitizing_org_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
            sa.Column("customer_org_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("description", sa.String(1000), nullable=True),
            sa.Column("proposed_end_date", sa.Date, nullable=True),
            sa.Column("s3_bucket_name", sa.String(255), nullable=True),
            sa.Column("s3_bucket_status", s3status_enum, nullable=False, server_default="provisioning"),
            sa.Column("stale_threshold_hours", sa.Float, nullable=False, server_default="8.0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

        op.create_table(
            "shifts",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=False, index=True),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("start_time", sa.Time, nullable=False),
            sa.Column("end_time", sa.Time, nullable=False),
            sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

        op.create_table(
            "project_shifts",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False, index=True),
            sa.Column("shift_id", sa.Integer, sa.ForeignKey("shifts.id"), nullable=False),
        )

        op.create_table(
            "user_project_assignments",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False, index=True),
            sa.Column("shift_id", sa.Integer, sa.ForeignKey("shifts.id"), nullable=False),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

        op.create_table(
            "document_types",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False, index=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("json_schema", sa.JSON, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

        op.create_table(
            "batches",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False, index=True),
            sa.Column("document_type_id", sa.Integer, sa.ForeignKey("document_types.id"), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("status", batchstatus_enum, nullable=False, server_default="draft"),
            sa.Column("aql_level_snapshot", sa.Float, nullable=True),
            sa.Column("aql_sample_size", sa.Integer, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

        op.create_table(
            "records",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("batch_id", sa.Integer, sa.ForeignKey("batches.id"), nullable=False, index=True),
            sa.Column("file_reference", sa.String(1024), nullable=True),
            sa.Column("indexed_data", sa.JSON, nullable=True),
            sa.Column("current_version", sa.Integer, nullable=False, server_default="1"),
            sa.Column("locked_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", recordstatus_enum, nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

        op.create_table(
            "record_versions",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("record_id", sa.Integer, sa.ForeignKey("records.id"), nullable=False, index=True),
            sa.Column("version_number", sa.Integer, nullable=False),
            sa.Column("indexed_data", sa.JSON, nullable=False),
            sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("reason", versionreason_enum, nullable=False),
        )

        op.create_table(
            "audit_logs",
            sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=False, index=True),
            sa.Column("entity_type", auditentity_enum, nullable=False),
            sa.Column("entity_id", sa.BigInteger, nullable=False, index=True),
            sa.Column("action", auditaction_enum, nullable=False),
            sa.Column("performed_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
            sa.Column("performed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
            sa.Column("old_value", sa.JSON, nullable=True),
            sa.Column("new_value", sa.JSON, nullable=True),
            sa.Column("metadata", sa.JSON, nullable=True),
        )

        op.create_table(
            "tasks",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("record_id", sa.Integer, sa.ForeignKey("records.id"), nullable=False, index=True),
            sa.Column("batch_id", sa.Integer, sa.ForeignKey("batches.id"), nullable=False, index=True),
            sa.Column("task_type", tasktype_enum, nullable=False),
            sa.Column("assigned_to", sa.Integer, sa.ForeignKey("users.id"), nullable=True, index=True),
            sa.Column("assigned_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
            sa.Column("status", taskstatus_enum, nullable=False, server_default="pending"),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=True, index=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("processing_time_seconds", sa.Integer, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

        op.create_table(
            "aql_configs",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), unique=True, nullable=False),
            sa.Column("current_status", aqlstatus_enum, nullable=False, server_default="normal"),
            sa.Column("consecutive_passes", sa.Integer, nullable=False, server_default="0"),
            sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default="0"),
            sa.Column("normal_aql", sa.Float, nullable=False, server_default="1.5"),
            sa.Column("tightened_aql", sa.Float, nullable=False, server_default="1.0"),
            sa.Column("reduced_aql", sa.Float, nullable=False, server_default="2.5"),
            sa.Column("passes_to_reduce", sa.Integer, nullable=False, server_default="5"),
            sa.Column("failures_to_tighten", sa.Integer, nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

        op.create_table(
            "batch_qc_results",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("batch_id", sa.Integer, sa.ForeignKey("batches.id"), unique=True, nullable=False),
            sa.Column("total_inspected", sa.Integer, nullable=False),
            sa.Column("defects_found", sa.Integer, nullable=False),
            sa.Column("acceptance_number", sa.Integer, nullable=False),
            sa.Column("aql_level_applied", sa.Float, nullable=False),
            sa.Column("outcome", sa.String(20), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("batch_qc_results")
    op.drop_table("aql_configs")
    op.drop_table("tasks")
    op.drop_table("audit_logs")
    op.drop_table("record_versions")
    op.drop_table("records")
    op.drop_table("batches")
    op.drop_table("document_types")
    op.drop_table("user_project_assignments")
    op.drop_table("project_shifts")
    op.drop_table("shifts")
    op.drop_table("projects")
    op.drop_table("users")
    op.drop_table("organizations")
    op.drop_table("tenants")

    for name in [
        "auditaction", "auditentitytype", "aqlstatus", "taskstatus", "tasktype",
        "versionreason", "recordstatus", "batchstatus", "s3bucketstatus",
        "portal", "userrole", "orgtype",
    ]:
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
