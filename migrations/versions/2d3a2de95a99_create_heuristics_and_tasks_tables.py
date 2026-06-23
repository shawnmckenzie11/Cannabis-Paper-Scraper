"""create_heuristics_and_tasks_tables

Revision ID: 2d3a2de95a99
Revises: 
Create Date: 2026-06-23 14:14:43.900091

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2d3a2de95a99'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema by creating heuristics_rules and background_tasks tables."""
    # 1. Create heuristics_rules table
    op.create_table(
        'heuristics_rules',
        sa.Column('rule_key', sa.String(length=100), primary_key=True),
        sa.Column('rule_value', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False)
    )

    # 2. Create background_tasks table
    op.create_table(
        'background_tasks',
        sa.Column('task_id', sa.String(length=100), primary_key=True),
        sa.Column('sa_task_type', sa.String(length=100), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('total_papers', sa.Integer(), nullable=True),
        sa.Column('processed_papers', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False)
    )


def downgrade() -> None:
    """Downgrade schema by dropping the tables."""
    op.drop_table('background_tasks')
    op.drop_table('heuristics_rules')
