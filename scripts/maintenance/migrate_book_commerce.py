"""Run the isolated book-commerce Alembic track without modifying the main version."""
import argparse
import json
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Integer, create_engine, inspect, text
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / 'src' / 'backend' / 'book_commerce_migrations'


def _run(engine, direction):
    config = Config()
    config.set_main_option('script_location', str(MIGRATIONS))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        if direction == 'upgrade':
            command.upgrade(config, 'head')
        else:
            command.downgrade(config, 'base')


def preflight(engine):
    with engine.connect() as connection:
        inspector = inspect(connection)
        names = inspector.get_table_names()
        feature_tables = sorted(name for name in names if name.startswith('bc_'))
        users = inspector.get_columns('users') if 'users' in names else []
        user_id = next((column for column in users if column['name'] == 'id'), None)
        user_pk = inspector.get_pk_constraint('users').get('constrained_columns') if users else []
        user_compatible = bool(user_id and isinstance(user_id['type'], Integer) and user_pk == ['id'])
        main_revisions = list(connection.execute(text('SELECT version_num FROM alembic_version')).scalars()) if 'alembic_version' in names else []
        feature_revisions = list(connection.execute(text('SELECT version_num FROM alembic_version_book_commerce')).scalars()) if 'alembic_version_book_commerce' in names else []
        return {'database': connection.engine.url.database, 'schema': inspector.default_schema_name,
                'dialect': connection.dialect.name, 'main_revisions': main_revisions,
                'feature_revisions': feature_revisions, 'feature_tables': feature_tables,
                'users_id_compatible': user_compatible,
                'ready_for_initial_upgrade': connection.dialect.name in ('postgresql', 'sqlite') and user_compatible and not feature_tables and not feature_revisions}


def upgrade(engine):
    _run(engine, 'upgrade')


def downgrade(engine):
    _run(engine, 'downgrade')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('direction', choices=('preflight', 'upgrade', 'downgrade'))
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / 'src' / 'backend'))
    from config.base import BaseConfig
    engine = create_engine(BaseConfig.SQLALCHEMY_DATABASE_URI, poolclass=NullPool)
    try:
        if args.direction == 'preflight':
            print(json.dumps(preflight(engine), ensure_ascii=False, indent=2))
            return
        _run(engine, args.direction)
    finally:
        engine.dispose()
    print('图书带货迁移已完成；主迁移版本未改动。')


if __name__ == '__main__':
    main()
