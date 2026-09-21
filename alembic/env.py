from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolved_sqlalchemy_url() -> str:
    database_url = os.environ.get('DATABASE_URL', '').strip()
    if database_url:
        return database_url

    database_path = os.environ.get('DATABASE_PATH', 'visbharat.db').strip() or 'visbharat.db'
    if database_path.startswith('sqlite:///'):
        return database_path
    return f"sqlite:///{database_path}"


config.set_main_option('sqlalchemy.url', _resolved_sqlalchemy_url())


def run_migrations_offline() -> None:
    url = config.get_main_option('sqlalchemy.url')
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
