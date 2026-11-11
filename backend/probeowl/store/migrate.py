# Author: @SaadZarook
"""Create, upgrade and check the store's schema with Alembic, or print it as SQL for a DBA."""

import io

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from probeowl.errors import ConfigError

VERSION_TABLE = "probeowl_schema_version"
DIALECTS = ("sqlite", "postgresql", "mssql", "oracle", "mariadb")


def _config(url: str, output: io.StringIO | None = None) -> Config:
    config = Config(output_buffer=output)
    config.set_main_option("script_location", "probeowl.store:migrations")
    # ConfigParser treats % as interpolation; a password in a URL may contain one.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def upgrade(engine: Engine) -> None:
    """Create or upgrade the store's tables to the newest schema."""
    config = _config(engine.url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def ddl(dialect: str) -> str:
    """Return the SQL that creates the newest schema on dialect, for a DBA to run."""
    if dialect not in DIALECTS:
        raise ConfigError(f"unknown store dialect {dialect!r}; use one of: {', '.join(DIALECTS)}")
    output = io.StringIO()
    command.upgrade(_config(f"{dialect}://", output), "head", sql=True)
    return output.getvalue()


def check_current(engine: Engine) -> None:
    """Raise ConfigError unless the store's schema is the newest one."""
    head = ScriptDirectory.from_config(_config(str(engine.url))).get_current_head()
    with engine.connect() as connection:
        current = MigrationContext.configure(
            connection, opts={"version_table": VERSION_TABLE}
        ).get_current_revision()
    if current is None:
        raise ConfigError("the store has no ProbeOwl tables yet; run: owl store init")
    if current != head:
        raise ConfigError(
            f"the store schema is at {current}, this release needs {head}; run: owl store upgrade"
        )
