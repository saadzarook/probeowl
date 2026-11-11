# Author: @SaadZarook
"""Alembic environment for the store. Run through probeowl.store.migrate, not the alembic CLI."""

from alembic import context

from probeowl.store.migrate import VERSION_TABLE
from probeowl.store.tables import metadata

config = context.config

if context.is_offline_mode():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=metadata,
        version_table=VERSION_TABLE,
        literal_binds=True,
    )
else:
    connection = config.attributes["connection"]
    context.configure(
        connection=connection,
        target_metadata=metadata,
        version_table=VERSION_TABLE,
        # SQLite cannot ALTER most things; batch mode rebuilds the table instead.
        render_as_batch=connection.dialect.name == "sqlite",
    )

with context.begin_transaction():
    context.run_migrations()
