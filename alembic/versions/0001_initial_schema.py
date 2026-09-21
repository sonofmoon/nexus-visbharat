"""Initial visbharat schema for PostgreSQL/SQLite via Alembic."""

from alembic import op
import sqlalchemy as sa


revision = '0001_initial_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('api_token', sa.Text(), nullable=False),
        sa.Column('api_token_hash', sa.Text(), nullable=True),
        sa.Column('token_last4', sa.Text(), nullable=True),
        sa.Column('token_rotated_at', sa.Text(), nullable=True),
        sa.Column('role', sa.Text(), nullable=False, server_default='analyst'),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.UniqueConstraint('api_token', name='uq_users_api_token'),
    )

    op.create_table(
        'citizen_requests',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('request_id', sa.Text(), nullable=False),
        sa.Column('source_channel', sa.Text(), nullable=False),
        sa.Column('input_language', sa.Text(), nullable=False),
        sa.Column('district', sa.Text(), nullable=False),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('lat', sa.Float(), nullable=False),
        sa.Column('lng', sa.Float(), nullable=False),
        sa.Column('original_text', sa.Text(), nullable=False),
        sa.Column('translated_text', sa.Text(), nullable=False),
        sa.Column('category', sa.Text(), nullable=False),
        sa.Column('urgency', sa.Text(), nullable=False),
        sa.Column('sentiment', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('submitted_by', sa.Text(), nullable=True),
        sa.Column('ai_metadata_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.UniqueConstraint('request_id', name='uq_citizen_requests_request_id'),
    )

    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('actor', sa.Text(), nullable=False),
        sa.Column('action', sa.Text(), nullable=False),
        sa.Column('resource_type', sa.Text(), nullable=False),
        sa.Column('resource_id', sa.Text(), nullable=True),
        sa.Column('details_json', sa.Text(), nullable=True),
        sa.Column('ip_address', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('audit_logs')
    op.drop_table('citizen_requests')
    op.drop_table('users')
