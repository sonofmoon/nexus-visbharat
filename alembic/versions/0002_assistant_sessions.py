"""Persistent citizen assistant drafts and transaction-linked receipts."""
from alembic import op
import sqlalchemy as sa
revision = '0002_assistant_sessions'
down_revision = '0001_initial_schema'
branch_labels = None
depends_on = None

def upgrade():
    if sa.inspect(op.get_bind()).has_table('assistant_sessions'):
        return
    op.create_table('assistant_sessions',
        sa.Column('session_id',sa.Text(),primary_key=True),
        sa.Column('token_hash',sa.Text(),nullable=False),
        sa.Column('draft_json',sa.Text(),nullable=False),
        sa.Column('state',sa.Text(),nullable=False),
        sa.Column('language',sa.Text(),nullable=False),
        sa.Column('version',sa.Integer(),nullable=False,server_default='0'),
        sa.Column('expires_at',sa.BigInteger(),nullable=False),
        sa.Column('busy_until',sa.BigInteger(),nullable=False,server_default='0'),
        sa.Column('lock_token',sa.Text()),sa.Column('last_turn_id',sa.Text()),
        sa.Column('last_turn_hash',sa.Text()),sa.Column('response_json',sa.Text()),
        sa.Column('request_id',sa.Text(),unique=True))
    op.create_index('idx_assistant_expiry','assistant_sessions',['expires_at'])

def downgrade():
    op.drop_index('idx_assistant_expiry',table_name='assistant_sessions')
    op.drop_table('assistant_sessions')
