"""Normalize account track labels into a hierarchy and many-to-many links."""
from datetime import datetime
import json
import re
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = 'bc_20261118_02'
down_revision = 'bc_20261118_01'
branch_labels = None
depends_on = None


def _segments(value):
    if not value:
        return []
    return [part.strip() for part in re.split(r'\s*[/／]\s*', value) if part.strip()]


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('bc_categories') or inspector.has_table('bc_account_categories'):
        raise RuntimeError('赛道关系表已存在，但迁移版本未记录；请先核对数据库状态')

    op.create_table(
        'bc_categories',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('parent_id', sa.String(length=36), nullable=True),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('path', sa.String(length=512), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['parent_id'], ['bc_categories.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('path', name='uq_bc_categories_path'),
    )
    op.create_index('ix_bc_categories_parent_name', 'bc_categories', ['parent_id', 'name'], unique=False)
    op.create_table(
        'bc_account_categories',
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('category_id', sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['bc_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['category_id'], ['bc_categories.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('account_id', 'category_id'),
    )
    op.create_index('ix_bc_account_categories_category_account', 'bc_account_categories', ['category_id', 'account_id'], unique=False)

    category_table = sa.table(
        'bc_categories',
        sa.column('id', sa.String(length=36)),
        sa.column('parent_id', sa.String(length=36)),
        sa.column('name', sa.String(length=128)),
        sa.column('path', sa.String(length=512)),
        sa.column('created_at', sa.DateTime()),
    )
    link_table = sa.table(
        'bc_account_categories',
        sa.column('account_id', sa.String(length=36)),
        sa.column('category_id', sa.String(length=36)),
    )

    now = datetime.utcnow()
    category_ids = {}
    links = []
    accounts = bind.execute(sa.text('SELECT id, category FROM bc_accounts ORDER BY id')).mappings()
    for account in accounts:
        parent_id = None
        path_parts = []
        leaf_id = None
        for name in _segments(account['category']):
            path_parts.append(name)
            path = ' / '.join(path_parts)
            category_id = category_ids.get(path)
            if category_id is None:
                category_id = str(uuid4())
                bind.execute(category_table.insert().values(
                    id=category_id,
                    parent_id=parent_id,
                    name=name,
                    path=path,
                    created_at=now,
                ))
                category_ids[path] = category_id
            parent_id = category_id
            leaf_id = category_id
        if leaf_id is not None:
            links.append({'account_id': account['id'], 'category_id': leaf_id})

    if links:
        bind.execute(link_table.insert(), links)


def downgrade():
    # SQLite DDL may persist even if a later migration fails. Check all data
    # protected by the preceding revision before removing any track tables.
    bind = op.get_bind()
    for name in ('bc_categories', 'bc_account_categories', 'bc_accounts', 'bc_books',
                 'bc_listings', 'bc_account_listings', 'bc_videos', 'bc_video_listing_links',
                 'bc_ingest_batches', 'bc_observations', 'bc_daily_metrics', 'bc_analysis_runs',
                 'bc_recommendations', 'bc_candidate_decisions'):
        if bind.execute(sa.text('SELECT 1 FROM ' + name + ' LIMIT 1')).first():
            raise RuntimeError('已有图书带货数据，禁止自动删除')
    states = bind.execute(sa.text('SELECT id, input_revision, active_rules_version, published_run_id, rules_config FROM bc_state')).all()
    if len(states) != 1:
        raise RuntimeError('图书带货状态异常，禁止自动删除')
    state = states[0]
    try:
        config = json.loads(state[4]) if isinstance(state[4], str) else state[4]
    except (TypeError, ValueError):
        raise RuntimeError('图书带货状态异常，禁止自动删除') from None
    if state[0] != 1 or state[1] != 0 or state[2] != 1 or state[3] is not None or config != {}:
        raise RuntimeError('已有图书带货数据，禁止自动删除')

    op.drop_index('ix_bc_account_categories_category_account', table_name='bc_account_categories')
    op.drop_table('bc_account_categories')
    op.drop_index('ix_bc_categories_parent_name', table_name='bc_categories')
    op.drop_table('bc_categories')
