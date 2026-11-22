from alembic import context
from sqlalchemy import text

connection = context.config.attributes['connection']
context.configure(connection=connection, target_metadata=None, version_table='alembic_version_book_commerce')
with context.begin_transaction():
    if connection.dialect.name == 'postgresql':
        connection.execute(text('SELECT pg_advisory_xact_lock(9026111801)'))
    context.run_migrations()
