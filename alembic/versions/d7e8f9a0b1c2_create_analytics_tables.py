"""create analytics tables

Revision ID: d7e8f9a0b1c2
Revises: e74fc920afdf
Create Date: 2026-10-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, Sequence[str], None] = 'e74fc920afdf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'analytics_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('visitor_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('engaged_ms', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('page_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('landing_path', sa.String(length=300), nullable=False),
        sa.Column('source', sa.String(length=40), nullable=False),
        sa.Column('medium', sa.String(length=40), nullable=False),
        sa.Column('referrer_host', sa.String(length=255), nullable=True),
        sa.Column('utm_campaign', sa.String(length=100), nullable=True),
        sa.Column('device', sa.String(length=16), nullable=False),
        sa.Column('browser', sa.String(length=32), nullable=False),
        sa.Column('os', sa.String(length=32), nullable=False),
        sa.Column('country', sa.String(length=2), nullable=True),
        sa.Column('city', sa.String(length=100), nullable=True),
        sa.Column('ip_hash', sa.String(length=64), nullable=True),
        sa.Column('is_returning', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('interacted', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('bot_score', sa.SmallInteger(), server_default='0', nullable=False),
        sa.Column('is_bot', sa.Boolean(), server_default='false', nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_analytics_sessions')),
    )
    op.create_index('ix_analytics_sessions_started_at', 'analytics_sessions', ['started_at'])
    op.create_index('ix_analytics_sessions_visitor_id', 'analytics_sessions', ['visitor_id'])

    op.create_table(
        'analytics_pageviews',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('session_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('path', sa.String(length=300), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('engaged_ms', sa.Integer(), server_default='0', nullable=False),
        sa.Column('max_scroll', sa.SmallInteger(), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(
            ['session_id'],
            ['analytics_sessions.id'],
            name=op.f('fk_analytics_pageviews_session_id_analytics_sessions'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_analytics_pageviews')),
    )
    op.create_index('ix_analytics_pageviews_session_id', 'analytics_pageviews', ['session_id'])
    op.create_index('ix_analytics_pageviews_path_occurred_at', 'analytics_pageviews', ['path', 'occurred_at'])
    op.create_index('ix_analytics_pageviews_occurred_at', 'analytics_pageviews', ['occurred_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('analytics_pageviews')
    op.drop_table('analytics_sessions')
