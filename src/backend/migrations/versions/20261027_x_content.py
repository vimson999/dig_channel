"""Create X content operations tables (frozen schema)."""
from alembic import op
import sqlalchemy as sa
revision = '20261027_x_content'
down_revision = '20261021_shengcai_catalog'
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table('x_viral_patterns'):
        op.create_table('x_viral_patterns',
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('category', sa.String(length=50), nullable=True),
            sa.Column('time_slot', sa.String(length=50), nullable=True),
            sa.Column('hook_formula', sa.Text(), nullable=True),
            sa.Column('body_formula', sa.Text(), nullable=True),
            sa.Column('cta_formula', sa.Text(), nullable=True),
            sa.Column('template_skeleton', sa.Text(), nullable=True),
            sa.Column('evidence_status', sa.String(length=20), nullable=True),
            sa.Column('avg_reply_rate', sa.Numeric(precision=8, scale=2), nullable=True),
            sa.Column('evidence_notes', sa.Text(), nullable=True),
            sa.Column('tags', sa.JSON(), nullable=False),
            sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
            sa.Column('is_mock', sa.Boolean(), nullable=False),
            sa.Column('source_batch', sa.String(length=100), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False))
        op.create_index('ix_x_viral_patterns_is_mock', 'x_viral_patterns', ['is_mock'])
    if not sa.inspect(bind).has_table('x_viral_cases'):
        op.create_table('x_viral_cases',
            sa.Column('pattern_id', sa.String(length=64), sa.ForeignKey('x_viral_patterns.id'), nullable=True),
            sa.Column('author_handle', sa.String(length=64), nullable=True),
            sa.Column('author_followers', sa.BigInteger(), nullable=True),
            sa.Column('author_name', sa.String(length=100), nullable=True),
            sa.Column('verified_badge', sa.String(length=30), nullable=True),
            sa.Column('media_type', sa.String(length=30), nullable=True),
            sa.Column('translated_text', sa.Text(), nullable=True),
            sa.Column('reusable_hook', sa.Text(), nullable=True),
            sa.Column('language', sa.String(length=20), nullable=True),
            sa.Column('raw_text', sa.Text(), nullable=False),
            sa.Column('why_it_worked', sa.Text(), nullable=True),
            sa.Column('is_negative_sample', sa.Boolean(), nullable=False),
            sa.Column('screenshot_path', sa.String(length=1000), nullable=True),
            sa.Column('source_url', sa.String(length=1000), nullable=True),
            sa.Column('published_at', sa.DateTime(), nullable=True),
            sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
            sa.Column('is_mock', sa.Boolean(), nullable=False),
            sa.Column('source_batch', sa.String(length=100), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('views', sa.BigInteger(), nullable=True),
            sa.Column('likes', sa.BigInteger(), nullable=True),
            sa.Column('replies', sa.BigInteger(), nullable=True),
            sa.Column('reposts', sa.BigInteger(), nullable=True),
            sa.Column('bookmarks', sa.BigInteger(), nullable=True),
            sa.Column('metrics_captured_at', sa.DateTime(), nullable=True))
        op.create_index('ix_x_viral_cases_pattern_id', 'x_viral_cases', ['pattern_id'])
        op.create_index('ix_x_viral_cases_is_mock', 'x_viral_cases', ['is_mock'])
    if not sa.inspect(bind).has_table('x_own_posts'):
        op.create_table('x_own_posts',
            sa.Column('pattern_id', sa.String(length=64), sa.ForeignKey('x_viral_patterns.id'), nullable=True),
            sa.Column('ref_case_ids', sa.JSON(), nullable=False),
            sa.Column('unresolved_case_ids', sa.JSON(), nullable=False),
            sa.Column('title', sa.String(length=200), nullable=True),
            sa.Column('core_interactors', sa.JSON(), nullable=False),
            sa.Column('total_blue_v_replies', sa.BigInteger(), nullable=True),
            sa.Column('screenshot_path', sa.String(length=1000), nullable=True),
            sa.Column('account_handle', sa.String(length=64), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=True),
            sa.Column('time_slot', sa.String(length=50), nullable=True),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('early_burst_notes', sa.Text(), nullable=True),
            sa.Column('early_replies', sa.BigInteger(), nullable=True),
            sa.Column('early_window_minutes', sa.Integer(), nullable=True),
            sa.Column('review_notes', sa.Text(), nullable=True),
            sa.Column('published_at', sa.DateTime(), nullable=True),
            sa.Column('source_url', sa.String(length=1000), nullable=True),
            sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
            sa.Column('is_mock', sa.Boolean(), nullable=False),
            sa.Column('source_batch', sa.String(length=100), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('views', sa.BigInteger(), nullable=True),
            sa.Column('likes', sa.BigInteger(), nullable=True),
            sa.Column('replies', sa.BigInteger(), nullable=True),
            sa.Column('reposts', sa.BigInteger(), nullable=True),
            sa.Column('bookmarks', sa.BigInteger(), nullable=True),
            sa.Column('metrics_captured_at', sa.DateTime(), nullable=True))
        op.create_index('ix_x_own_posts_pattern_id', 'x_own_posts', ['pattern_id'])
        op.create_index('ix_x_own_posts_is_mock', 'x_own_posts', ['is_mock'])
    if not sa.inspect(bind).has_table('x_reply_tactics'):
        op.create_table('x_reply_tactics',
            sa.Column('tactic_name', sa.String(length=100), nullable=False),
            sa.Column('trigger_condition', sa.Text(), nullable=True),
            sa.Column('reply_logic', sa.Text(), nullable=True),
            sa.Column('sample_replies', sa.JSON(), nullable=False),
            sa.Column('frequent_friends', sa.JSON(), nullable=False),
            sa.Column('scenario_type', sa.String(length=50), nullable=True),
            sa.Column('algorithm_purpose', sa.Text(), nullable=True),
            sa.Column('priority', sa.Integer(), nullable=True),
            sa.Column('action_type', sa.String(length=20), nullable=True),
            sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
            sa.Column('is_mock', sa.Boolean(), nullable=False),
            sa.Column('source_batch', sa.String(length=100), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False))
        op.create_index('ix_x_reply_tactics_is_mock', 'x_reply_tactics', ['is_mock'])

def downgrade():
    op.drop_table('x_reply_tactics')
    op.drop_table('x_own_posts')
    op.drop_table('x_viral_cases')
    op.drop_table('x_viral_patterns')
