from pathlib import Path
import sys

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Column, Integer, MetaData, Table, create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'backend'))
from utils.migration_filters import exclude_book_commerce


def test_main_autogenerate_ignores_feature_tables_but_keeps_other_changes():
    engine = create_engine('sqlite://')
    metadata = MetaData()
    Table('bc_example', metadata, Column('id', Integer, primary_key=True))
    Table('ordinary_table', metadata, Column('id', Integer, primary_key=True))
    with engine.connect() as connection:
        ctx = MigrationContext.configure(connection, opts={'include_object': exclude_book_commerce})
        changes = compare_metadata(ctx, metadata)
    tables = [entry[1].name for entry in changes if entry[0] == 'add_table']
    assert tables == ['ordinary_table']
    engine.dispose()


def test_main_autogenerate_preserves_existing_feature_version_table():
    engine = create_engine('sqlite://')
    existing = MetaData()
    Table('alembic_version_book_commerce', existing, Column('version_num', Integer, primary_key=True))
    Table('bc_example', existing, Column('id', Integer, primary_key=True))
    Table('ordinary_obsolete_table', existing, Column('id', Integer, primary_key=True))
    existing.create_all(engine)
    with engine.connect() as connection:
        ctx = MigrationContext.configure(connection, opts={'include_object': exclude_book_commerce})
        changes = compare_metadata(ctx, MetaData())
    removed = [entry[1].name for entry in changes if entry[0] == 'remove_table']
    assert removed == ['ordinary_obsolete_table']
    engine.dispose()
