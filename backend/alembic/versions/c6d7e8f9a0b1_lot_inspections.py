"""add lot_inspections, lot_inspection_items, lot_inspection_field_results

Revision ID: c6d7e8f9a0b1
Revises: 8ecac36541a6
Create Date: 2026-10-30 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c6d7e8f9a0b1'
down_revision: Union[str, None] = '8ecac36541a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    ]


def upgrade() -> None:
    # Outgoing-inspection audit events are keyed to the lot itself, not
    # piggy-backed on entity_type='record' with a lot id (which would surface
    # them in the history of whichever record shares that id).
    op.execute("ALTER TYPE auditentitytype ADD VALUE IF NOT EXISTS 'lot'")

    op.create_table(
        'lot_inspections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('lot_id', sa.Integer(), nullable=False),
        sa.Column('attempt_number', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.Enum('in_progress', 'passed', 'failed', 'cancelled', name='lotinspectionstatus'),
            nullable=False,
        ),
        sa.Column('aql_level', sa.Float(), nullable=False),
        sa.Column('lot_size', sa.Integer(), nullable=False),
        sa.Column('code_letter', sa.String(length=2), nullable=False),
        sa.Column('sample_size', sa.Integer(), nullable=False),
        sa.Column('acceptance_number', sa.Integer(), nullable=False),
        sa.Column('rejection_number', sa.Integer(), nullable=False),
        sa.Column('random_seed', sa.String(length=64), nullable=False),
        sa.Column('critical_defect_count', sa.Integer(), nullable=True),
        sa.Column('minor_defect_count', sa.Integer(), nullable=True),
        sa.Column('defective_record_count', sa.Integer(), nullable=True),
        sa.Column('summary_note', sa.String(length=2048), nullable=True),
        sa.Column('inspected_by', sa.Integer(), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.ForeignKeyConstraint(['lot_id'], ['lots.id'], ),
        sa.ForeignKeyConstraint(['inspected_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('lot_id', 'attempt_number', name='uq_lot_inspection_attempt'),
    )
    op.create_index(op.f('ix_lot_inspections_tenant_id'), 'lot_inspections', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_lot_inspections_lot_id'), 'lot_inspections', ['lot_id'], unique=False)

    op.create_table(
        'lot_inspection_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('inspection_id', sa.Integer(), nullable=False),
        sa.Column('record_id', sa.Integer(), nullable=False),
        sa.Column('document_type_id', sa.Integer(), nullable=True),
        sa.Column('record_version', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.Enum('pending', 'accepted', 'defective', name='inspectionitemstatus'),
            nullable=False,
        ),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(['inspection_id'], ['lot_inspections.id'], ),
        sa.ForeignKeyConstraint(['record_id'], ['records.id'], ),
        sa.ForeignKeyConstraint(['document_type_id'], ['document_types.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('inspection_id', 'record_id', name='uq_lot_inspection_item'),
    )
    op.create_index(op.f('ix_lot_inspection_items_inspection_id'), 'lot_inspection_items', ['inspection_id'], unique=False)
    op.create_index(op.f('ix_lot_inspection_items_record_id'), 'lot_inspection_items', ['record_id'], unique=False)

    op.create_table(
        'lot_inspection_field_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('field_key', sa.String(length=255), nullable=False),
        # qcfieldstatus already exists (e1f2a3b4c5d6_qc_field_results).
        sa.Column(
            'status',
            postgresql.ENUM('accepted', 'defective', name='qcfieldstatus', create_type=False),
            nullable=False,
        ),
        sa.Column('is_critical', sa.Boolean(), nullable=False),
        sa.Column('note', sa.String(length=1024), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(['item_id'], ['lot_inspection_items.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_lot_inspection_field_results_item_id'), 'lot_inspection_field_results', ['item_id'], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_lot_inspection_field_results_item_id'), table_name='lot_inspection_field_results')
    op.drop_table('lot_inspection_field_results')
    op.drop_index(op.f('ix_lot_inspection_items_record_id'), table_name='lot_inspection_items')
    op.drop_index(op.f('ix_lot_inspection_items_inspection_id'), table_name='lot_inspection_items')
    op.drop_table('lot_inspection_items')
    op.drop_index(op.f('ix_lot_inspections_lot_id'), table_name='lot_inspections')
    op.drop_index(op.f('ix_lot_inspections_tenant_id'), table_name='lot_inspections')
    op.drop_table('lot_inspections')
    sa.Enum(name='inspectionitemstatus').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='lotinspectionstatus').drop(op.get_bind(), checkfirst=True)
