"""Standalone PostgreSQL Alembic schema (SQLite legacy 0006 remains untouched)."""
from logging.config import fileConfig
import os
from alembic import context
from sqlalchemy import create_engine, pool
config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
url = os.environ.get('PG_MIGRATION_DSN', '')
if not url:
    raise RuntimeError('PG_MIGRATION_DSN is required; use postgres OWNER role only for migrations')

def run_migrations_online():
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=None, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()

if context.is_offline_mode():
    context.configure(url=url, literal_binds=True, dialect_opts={'paramstyle':'named'})
    with context.begin_transaction():
        context.run_migrations()
else:
    run_migrations_online()
