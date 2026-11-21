"""Apply versioned SQL to the configured PostgreSQL database."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import psycopg

MIGRATION_DIRECTORY = Path(__file__).resolve().parent / "migrations"
MIGRATION_LOCK = 716059331


class MigrationError(Exception):
    """Installed schema differs from the SQL shipped with this build."""


def migration_files() -> dict[str, str]:
    files = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(MIGRATION_DIRECTORY.glob("*.sql"))
    }
    if not files or any(not re.fullmatch(r"\d{3}_[a-z0-9_]+\.sql", name) for name in files):
        raise MigrationError("Expected numbered SQL migrations")
    prefixes = {name[:3] for name in files}
    expected = {f"{version:03d}" for version in range(1, len(files) + 1)}
    if prefixes != expected:
        raise MigrationError("Migration numbering must be contiguous")
    return files


def migration_checksum(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def migrate(database_url: str) -> list[str]:
    files = migration_files()
    with psycopg.connect(database_url, connect_timeout=3) as connection:
        connection.execute("SET LOCAL statement_timeout = '30s'")
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
        connection.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name text PRIMARY KEY,
                checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """)
        applied: dict[str, str] = dict(
            connection.execute("SELECT name, checksum FROM schema_migrations")
        )
        expected_prefix = list(files)[: len(applied)]
        if sorted(applied) != expected_prefix:
            raise MigrationError(
                "Applied migrations must match the beginning of the migration history"
            )
        for name, checksum in applied.items():
            if checksum != migration_checksum(files[name]):
                raise MigrationError(f"Applied migration checksum mismatch for {name}")
        pending = [name for name in files if name not in applied]
        for name in pending:
            # SQL comes only from packaged, version-controlled migration files.
            connection.execute(files[name])
            connection.execute(
                "INSERT INTO schema_migrations (name, checksum) VALUES (%s, %s)",
                (name, migration_checksum(files[name])),
            )
    return pending


def main() -> None:
    try:
        database_url = os.environ["AGENTPROOF_DATABASE_URL"]
    except KeyError:
        raise SystemExit("AGENTPROOF_DATABASE_URL is required") from None
    try:
        applied = migrate(database_url)
    except MigrationError as error:
        raise SystemExit(str(error)) from None
    except psycopg.Error as error:
        raise SystemExit(f"Migration database operation failed ({type(error).__name__})") from None
    print(f"Applied {', '.join(applied)}" if applied else "Database migrations are current")


if __name__ == "__main__":
    main()
