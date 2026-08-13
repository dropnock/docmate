"""add qc_field_results table

Revision ID: e1f2a3b4c5d6
Revises: d7e8f9a0b1c2
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'd7e8f9a0b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'qc_field_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('record_id', sa.Integer(), nullable=False),
        sa.Column('lot_id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('field_key', sa.String(length=255), nullable=False),
        sa.Column('status', sa.Enum('accepted', 'defective', name='qcfieldstatus'), nullable=False),
        sa.Column('is_critical', sa.Boolean(), nullable=False),
        sa.Column('note', sa.String(length=1024), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
        sa.ForeignKeyConstraint(['record_id'], ['records.id'], ),
        sa.ForeignKeyConstraint(['lot_id'], ['lots.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_qc_field_results_task_id'), 'qc_field_results', ['task_id'], unique=False)
    op.create_index(op.f('ix_qc_field_results_record_id'), 'qc_field_results', ['record_id'], unique=False)
    op.create_index(op.f('ix_qc_field_results_lot_id'), 'qc_field_results', ['lot_id'], unique=False)
    op.create_index(op.f('ix_qc_field_results_tenant_id'), 'qc_field_results', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_qc_field_results_tenant_id'), table_name='qc_field_results')
    op.drop_index(op.f('ix_qc_field_results_lot_id'), table_name='qc_field_results')
    op.drop_index(op.f('ix_qc_field_results_record_id'), table_name='qc_field_results')
    op.drop_index(op.f('ix_qc_field_results_task_id'), table_name='qc_field_results')
    op.drop_table('qc_field_results')
    sa.Enum(name='qcfieldstatus').drop(op.get_bind(), checkfirst=True)
