from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src' / 'backend'))
sys.path.insert(0, str(ROOT / 'scripts' / 'maintenance'))
from migrate_book_commerce import upgrade, downgrade


def test_isolated_migration_keeps_existing_alembic_revision(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'migration.db'))
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))
        connection.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
        connection.execute(text("INSERT INTO alembic_version VALUES ('20261112_upower_topics')"))
    upgrade(engine)
    upgrade(engine)
    with engine.connect() as connection:
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '20261112_upower_topics'
        assert connection.execute(text('SELECT input_revision FROM bc_state WHERE id=1')).scalar_one() == 0
        assert len([t for t in inspect(connection).get_table_names() if t.startswith('bc_')]) == 15
    downgrade(engine)
    with engine.connect() as connection:
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '20261112_upower_topics'
        assert not [t for t in inspect(connection).get_table_names() if t.startswith('bc_')]
    engine.dispose()


def test_downgrade_refuses_to_discard_business_data(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'migration.db'))
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))
    upgrade(engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO bc_accounts (id, source, source_key, platform, name, created_at, updated_at) VALUES ('a','s','a','wechat_channels','账号','2026-01-01','2026-01-01')"))
    original_tables = set(inspect(engine).get_table_names())
    with pytest.raises(RuntimeError, match='数据'):
        downgrade(engine)
    assert set(inspect(engine).get_table_names()) == original_tables
    with engine.connect() as connection:
        assert connection.execute(text('SELECT version_num FROM alembic_version_book_commerce')).scalar_one() == 'bc_20261118_02'
        assert connection.execute(text("SELECT name FROM bc_accounts WHERE id='a'")).scalar_one() == '账号'
    engine.dispose()


def test_downgrade_refuses_to_discard_category_data(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'migration.db'))
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))
    upgrade(engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO bc_categories (id, name, path, created_at) VALUES ('c','赛道','赛道','2026-01-01')"))
    with pytest.raises(RuntimeError, match='数据'):
        downgrade(engine)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT path FROM bc_categories WHERE id='c'")).scalar_one() == '赛道'
        assert connection.execute(text('SELECT version_num FROM alembic_version_book_commerce')).scalar_one() == 'bc_20261118_02'
    engine.dispose()


def test_postgres_track_preserves_unknown_main_revision():
    import os
    from sqlalchemy.engine import make_url
    url = os.getenv('BOOK_COMMERCE_TEST_DATABASE_URL')
    if not url:
        pytest.skip('requires isolated PostgreSQL')
    parsed = make_url(url)
    if parsed.host not in ('localhost', '127.0.0.1') or not parsed.database.startswith('bc_test_'):
        raise RuntimeError('requires disposable localhost bc_test_* database')
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))
        connection.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
        connection.execute(text("INSERT INTO alembic_version VALUES ('20261112_upower_topics')"))
    upgrade(engine)
    with engine.connect() as connection:
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '20261112_upower_topics'
        assert connection.execute(text('SELECT version_num FROM alembic_version_book_commerce')).scalar_one() == 'bc_20261118_02'
        assert len([t for t in inspect(connection).get_table_names() if t.startswith('bc_')]) == 15
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext
        from models import db
        context = MigrationContext.configure(connection, opts={'include_object':lambda obj,name,type_,reflected,compare_to: (name.startswith('bc_') if type_=='table' else True), 'compare_type':True})
        assert compare_metadata(context, db.metadata) == []
    downgrade(engine)
    with engine.begin() as connection:
        connection.execute(text('DROP TABLE alembic_version'))
        connection.execute(text('DROP TABLE users'))
    engine.dispose()


def test_migration_refuses_incompatible_users_table(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'migration.db'))
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE users (id TEXT PRIMARY KEY)'))
    with pytest.raises(RuntimeError, match='users.id'):
        upgrade(engine)
    assert not [name for name in inspect(engine).get_table_names() if name.startswith('bc_')]
    engine.dispose()


def test_read_only_preflight_reports_unknown_main_revision_without_changes(tmp_path):
    from migrate_book_commerce import preflight
    engine = create_engine('sqlite:///' + str(tmp_path / 'migration.db'))
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))
        connection.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
        connection.execute(text("INSERT INTO alembic_version VALUES ('20261112_upower_topics')"))
    result = preflight(engine)
    assert result['ready_for_initial_upgrade'] is True
    assert result['main_revisions'] == ['20261112_upower_topics']
    assert set(inspect(engine).get_table_names()) == {'users', 'alembic_version'}
    engine.dispose()
