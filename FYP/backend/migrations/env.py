"""Alembic environment — reads DB config from .env"""
import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

load_dotenv(override=True)

config = context.config

# Inject env vars into the alembic URL template
config.set_section_option("alembic", "DB_USER",     os.getenv("DB_USER",     "anpr_user"))
config.set_section_option("alembic", "DB_PASSWORD", os.getenv("DB_PASSWORD", "anpr_pass123"))
config.set_section_option("alembic", "DB_HOST",     os.getenv("DB_HOST",     "localhost"))
config.set_section_option("alembic", "DB_PORT",     os.getenv("DB_PORT",     "3306"))
config.set_section_option("alembic", "DB_NAME",     os.getenv("DB_NAME",     "anpr_db"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
