"""Small, idempotent SQLite migrations needed by the current application."""

from contextlib import closing, contextmanager
from datetime import datetime
import fcntl
import os
from pathlib import Path
import sqlite3

from sqlalchemy import inspect


_REQUIRED_TABLES = {
    "transactions",
    "transfers",
    "transfer_items",
    "transfer_requests",
    "materials",
}


def _sqlite_path(engine):
    if engine.dialect.name != "sqlite":
        return None
    database = engine.url.database
    if not database or database == ":memory:" or database.startswith("file:"):
        return None
    return Path(database).expanduser().resolve()


@contextmanager
def _database_lock(path):
    # Gunicorn workers can import the app at the same time during a deploy.
    with path.open("rb") as database_file:
        fcntl.flock(database_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(database_file.fileno(), fcntl.LOCK_UN)


def _integrity_check(path):
    with closing(sqlite3.connect(str(path), timeout=10)) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    return result[0] if result else "no result"


def _ensure_backup(database_path):
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = database_path.with_name(
        f"{database_path.name}.pre_schema_migration_{suffix}"
    )

    with closing(sqlite3.connect(str(database_path), timeout=10)) as source:
        with closing(sqlite3.connect(str(backup_path), timeout=10)) as target:
            source.backup(target)
    os.chmod(backup_path, 0o600)

    integrity = _integrity_check(backup_path)
    if integrity != "ok":
        raise RuntimeError(
            f"Schema migration stopped: backup integrity check failed: {integrity}"
        )
    return backup_path


def _table_columns(connection, table_name):
    return {
        row[1]
        for row in connection.exec_driver_sql(
            f'PRAGMA table_info("{table_name}")'
        ).fetchall()
    }


def migrate_request_item_schema(engine):
    """Upgrade the legacy single-material request schema before ORM startup."""
    database_path = _sqlite_path(engine)
    if not database_path or not database_path.is_file():
        return

    with _database_lock(database_path):
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            if not tables.intersection(_REQUIRED_TABLES):
                # A new empty database will be initialized by db.create_all().
                return
            missing_tables = _REQUIRED_TABLES - tables
            if missing_tables:
                names = ", ".join(sorted(missing_tables))
                raise RuntimeError(
                    f"Schema migration stopped: expected tables are missing: {names}"
                )
            request_item_columns = (
                _table_columns(connection, "transfer_request_items")
                if "transfer_request_items" in tables
                else set()
            )
            needs_migration = (
                "request_item_id" not in _table_columns(connection, "transactions")
                or "request_id" not in _table_columns(connection, "transfers")
                or "request_item_id" not in _table_columns(connection, "transfer_items")
                or "transfer_request_items" not in tables
                or "transferred_material_id" not in request_item_columns
            )

        if not needs_migration:
            return

        backup_path = _ensure_backup(database_path)

        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS transfer_request_items (
                    id INTEGER NOT NULL PRIMARY KEY,
                    request_id INTEGER NOT NULL REFERENCES transfer_requests(id),
                    material_id INTEGER NOT NULL REFERENCES materials(id),
                    requested_quantity INTEGER NOT NULL,
                    transferred_quantity INTEGER,
                    note TEXT,
                    created_at DATETIME,
                    transferred_material_id INTEGER REFERENCES materials(id)
                )
                """
            )

            item_columns = _table_columns(connection, "transfer_request_items")
            required_item_columns = {
                "request_id",
                "material_id",
                "requested_quantity",
                "transferred_quantity",
                "note",
                "created_at",
            }
            missing_item_columns = required_item_columns - item_columns
            if missing_item_columns:
                names = ", ".join(sorted(missing_item_columns))
                raise RuntimeError(
                    "Schema migration stopped: transfer_request_items has an "
                    f"unexpected shape; missing columns: {names}"
                )
            if "transferred_material_id" not in item_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE transfer_request_items "
                    "ADD COLUMN transferred_material_id INTEGER REFERENCES materials(id)"
                )

            for table, column, reference in (
                ("transactions", "request_item_id", "transfer_request_items(id)"),
                ("transfers", "request_id", "transfer_requests(id)"),
                ("transfer_items", "request_item_id", "transfer_request_items(id)"),
            ):
                if column not in _table_columns(connection, table):
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {column} INTEGER REFERENCES {reference}"
                    )

            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_transfer_request_items_request_id "
                "ON transfer_request_items(request_id)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_transfer_request_items_material_id "
                "ON transfer_request_items(material_id)"
            )

            # Legacy requests contained one material and quantity on the parent row.
            # Preserve that information as a single child item; don't infer historical
            # transfer or transaction links that were never stored reliably.
            connection.exec_driver_sql(
                """
                INSERT INTO transfer_request_items
                    (request_id, material_id, requested_quantity, note, created_at)
                SELECT request.id, request.material_id, request.quantity,
                       request.note, request.created_at
                FROM transfer_requests AS request
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM transfer_request_items AS item
                    WHERE item.request_id = request.id
                )
                """
            )

            integrity = connection.exec_driver_sql("PRAGMA integrity_check").scalar()
            if integrity != "ok":
                raise RuntimeError(
                    f"Schema migration stopped: integrity check failed: {integrity}"
                )

        print(f"SQLite request schema migrated. Verified backup: {backup_path}")
