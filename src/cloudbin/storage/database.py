from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATABASE_PATH = Path("data/vault.sqlite")


class DatabaseError(RuntimeError):
    """Base exception for SecureVault database errors."""


class ArchiveNotFoundError(DatabaseError):
    """Raised when an archive record cannot be found."""


class DuplicateArchiveError(DatabaseError):
    """Raised when an archive already exists for the same path and SHA-256."""


@dataclass(frozen=True)
class ArchiveRecord:
    id: int
    original_filename: str
    original_abspath: str
    cloud_relpath: str
    size_bytes: int
    sha256_hash: str
    status: str
    source_type: str
    created_at: str
    updated_at: str


class Database:
    VALID_STATUSES = {"PENDING", "ARCHIVED", "RESTORED", "DELETED"}

    VALID_SOURCE_TYPES = {"TRASH", "VAULTDROP"}

    def __init__(self, database_path: str | Path = DEFAULT_DATABASE_PATH) -> None:
        self.database_path = Path(database_path).expanduser()

        if not self.database_path.is_absolute():
            self.database_path = (Path.cwd() / self.database_path).resolve()

        self.database_path.parent.mkdir(parents=True, exist_ok=True)

        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:

        with self._connect() as connection:
            connection.execute("""
                               CREATE TABLE IF NOT EXISTS vault_archives
                               (
                                   id                INTEGER PRIMARY KEY AUTOINCREMENT,
                                   original_filename TEXT    NOT NULL,
                                   original_abspath  TEXT    NOT NULL,
                                   cloud_relpath     TEXT    NOT NULL,
                                   size_bytes        INTEGER NOT NULL CHECK (size_bytes >= 0),
                                   sha256_hash       TEXT    NOT NULL,
                                   status            TEXT    NOT NULL
                                       CHECK (status IN ('PENDING', 'ARCHIVED', 'RESTORED', 'DELETED')),
                                   source_type       TEXT    NOT NULL CHECK ( source_type IN ('TRASH', 'VAULTDROP')),
                                   created_at        TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                                   updated_at        TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                                   UNIQUE (original_abspath, sha256_hash)
                               )
                               """)

            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_vault_archives_status ON vault_archives (status) """
            )

            connection.execute(
                """ CREATE INDEX IF NOT EXISTS idx_vault_archives_filename ON vault_archives (original_filename)""")

            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_vault_archives_source_path ON vault_archives (original_abspath) """)

            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_vault_archives_cloud_path ON vault_archives (cloud_relpath)""")

    @classmethod
    def _validate_status(cls, status: str) -> None:
        if status not in cls.VALID_STATUSES:
            raise ValueError(f"Invalid archive status: {status}")

    @classmethod
    def _validate_source_type(cls, source_type: str, ) -> None:
        if source_type not in cls.VALID_SOURCE_TYPES:
            raise ValueError(f"Invalid source type: {source_type}")

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ArchiveRecord:
        return ArchiveRecord(id=row["id"], original_filename=row["original_filename"],
                             original_abspath=row["original_abspath"],
                             cloud_relpath=row["cloud_relpath"], size_bytes=row["size_bytes"],
                             sha256_hash=row["sha256_hash"], status=row["status"],
                             source_type=row["source_type"], created_at=row["created_at"], updated_at=row["updated_at"])

    def create_archive(self, *, original_filename: str, original_abspath: str, cloud_relpath: str, size_bytes: int,
                       sha256_hash: str, status: str = "PENDING", source_type: str) -> int:

        self._validate_status(status)
        self._validate_source_type(source_type)

        if not original_filename:
            raise ValueError("original_filename cannot be empty.")

        if not original_abspath:
            raise ValueError("original_abspath cannot be empty.")

        if not cloud_relpath:
            raise ValueError("cloud_relpath cannot be empty.")

        if size_bytes < 0:
            raise ValueError("size_bytes cannot be negative.")

        if not sha256_hash:
            raise ValueError("sha256_hash cannot be empty.")

        try:
            with self._connect() as connection:
                cursor = connection.execute("""INSERT INTO vault_archives (original_filename, original_abspath,
                                                                           cloud_relpath,
                                                                           size_bytes, sha256_hash, status, source_type)
                                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                            (original_filename, original_abspath, cloud_relpath, size_bytes,
                                             sha256_hash,
                                             status, source_type))
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise DuplicateArchiveError("An archive already exists for this source path and SHA-256 hash.") from exc

            raise DatabaseError("Failed to create archive record.") from exc

    def get_archive(self, archive_id: int) -> ArchiveRecord:
        """Return one archive record by ID."""

        with self._connect() as connection:
            row = connection.execute("""SELECT id,
                                               original_filename,
                                               original_abspath,
                                               cloud_relpath,
                                               size_bytes,
                                               sha256_hash,
                                               status,
                                               source_type,
                                               created_at,
                                               updated_at
                                        FROM vault_archives
                                        WHERE id = ? """, (archive_id,)).fetchone()

        if row is None:
            raise ArchiveNotFoundError(f"Archive not found: {archive_id}")

        return self._row_to_record(row)

    def find_by_source_and_hash(self, original_abspath: str, sha256_hash: str) -> ArchiveRecord | None:
        with self._connect() as connection:
            row = connection.execute("""
                                     SELECT id,
                                            original_filename,
                                            original_abspath,
                                            cloud_relpath,
                                            size_bytes,
                                            sha256_hash,
                                            status,
                                            source_type,
                                            created_at,
                                            updated_at
                                     FROM vault_archives
                                     WHERE original_abspath = ?
                                       AND sha256_hash = ?
                                     LIMIT 1  """, (original_abspath, sha256_hash), ).fetchone()

        if row is None:
            return None

        return self._row_to_record(row)

    def list_archives(self, *, status: str | None = None, source_type: str | None = None) -> list[ArchiveRecord]:

        if status is not None:
            self._validate_status(status)

        if source_type is not None:
            self._validate_source_type(source_type)

        query = """SELECT id,
                          original_filename,
                          original_abspath,
                          cloud_relpath,
                          size_bytes,
                          sha256_hash,
                          status,
                          source_type,
                          created_at,
                          updated_at
                   FROM vault_archives"""

        conditions: list[str] = []
        parameters: list[str] = []

        if status is not None:
            conditions.append("status = ?")
            parameters.append(status)

        if source_type is not None:
            conditions.append("source_type = ?")
            parameters.append(source_type)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY id DESC"

        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()

        return [self._row_to_record(row) for row in rows]

    def search_archives(self, query: str) -> list[ArchiveRecord]:

        if not query:
            return []

        escaped = (query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))

        pattern = f"%{escaped}%"

        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id,
                          original_filename,
                          original_abspath,
                          cloud_relpath,
                          size_bytes,
                          sha256_hash,
                          status,
                          source_type,
                          created_at,
                          updated_at
                   FROM vault_archives
                   WHERE original_filename LIKE ? ESCAPE
                         '\\' OR original_abspath LIKE ? ESCAPE '\\' OR cloud_relpath LIKE ? ESCAPE '\\'
                   ORDER BY id DESC """, (pattern, pattern, pattern)).fetchall()

        return [self._row_to_record(row) for row in rows]

    def update_status(self, archive_id: int, status: str) -> None:
        self._validate_status(status)

        with self._connect() as connection:
            cursor = connection.execute("""UPDATE vault_archives
                                           SET status     = ?,
                                               updated_at = CURRENT_TIMESTAMP
                                           WHERE id = ? """, (status, archive_id))

            if cursor.rowcount == 0:
                raise ArchiveNotFoundError(f"Archive not found: {archive_id}")
