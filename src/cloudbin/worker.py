from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from cloudbin.storage.database import (ArchiveRecord, Database, DuplicateArchiveError)
from cloudbin.storage.rclone import RcloneClient

logger = logging.getLogger(__name__)

HASH_CHUNK_SIZE = 64 * 1024
STUB_SUFFIX = ".sv"


class WorkerError(RuntimeError):
    """Base exception for SecureVault worker failures."""


class WorkerService:
    """
    Orchestrates SecureVault file archiving.

    The worker coordinates the database and rclone client but does not
    implement SQLite operations or remote-storage operations itself.

    Processing invariant:

        local file
            -> hash
            -> PENDING
            -> upload
            -> verify
            -> ARCHIVED
            -> local stub
   The local source file is replaced by a small SecureVault stub only
after the cloud upload has been successfully verified and the archive
has been committed as ARCHIVED.
    """

    def __init__(self, database: Database, storage: RcloneClient, vault_root: str | Path) -> None:
        self.database = database
        self.storage = storage
        self.vault_root = Path(vault_root).expanduser().resolve()

    def process_file(self, path: str | Path) -> None:
        source = Path(path).expanduser().resolve()
        if not source.exists():
            logger.warning("File no longer exists: %s", source)
            return

        if not source.is_file():
            logger.debug("Ignoring non-file path: %s", source)
            return
        if source.name.endswith(STUB_SUFFIX):
            logger.debug("Ignoring SecureVault stub: %s", source)
            return

        try:
            size_bytes, sha256_hash = self._calculate_file_hash(source)
            existing = self.database.find_by_source_and_hash(str(source), sha256_hash)
            if existing is not None:
                self._handle_existing_archive(existing, source, sha256_hash)
                return

            cloud_relpath = self._build_cloud_path(source)

            try:
                archive_id = self.database.create_archive(original_filename=source.name, original_abspath=str(source),
                                                          cloud_relpath=cloud_relpath,
                                                          size_bytes=size_bytes, sha256_hash=sha256_hash,
                                                          status="PENDING", source_type="VAULTDROP")
            except DuplicateArchiveError:
                existing = self.database.find_by_source_and_hash(str(source), sha256_hash)
                if existing is None:
                    raise
                self._handle_existing_archive(existing, source)
                return

            self._archive_pending_file(archive_id=archive_id, source=source, cloud_relpath=cloud_relpath,
                                       size_bytes=size_bytes, sha256_hash=sha256_hash)

        except Exception:
            # Most importantly: never delete the source file here.
            logger.exception("Failed to archive file: %s", source)

    def process_path(self, path: str | Path) -> None:
        source = Path(path).expanduser().resolve()
        if not source.exists():
            logger.warning("Path no longer exists: %s", source)
            return

        if source.is_file():
            self.process_file(source)
            return

        if not source.is_dir():
            logger.debug("Ignoring unsupported path: %s", source)
            return

        for child in source.rglob("*"):
            if child.is_file():
                self.process_file(child)

    def _archive_pending_file(self, *, archive_id: int, source: Path, cloud_relpath: str, size_bytes: int,
                              sha256_hash: str) -> None:
        try:
            self.storage.upload_to_drive(source, cloud_relpath)
            self.storage.verify_cloud_file(cloud_relpath, expected_size=self._expected_remote_size(source, size_bytes))
            self.database.update_status(archive_id, "ARCHIVED")
        except Exception:
            logger.exception("Archive failed; keeping local file: %s", source)
            return

        try:
            self._create_stub(archive_id=archive_id, source=source, cloud_relpath=cloud_relpath,
                              sha256_hash=sha256_hash)
        except Exception:
            logger.exception("Archive succeeded but creating local stub failed: %s", source)
            return

        self._delete_source(source)

    def _handle_existing_archive(self, existing: ArchiveRecord, source: Path, sha256_hash: str) -> None:
        if existing.status == "ARCHIVED":
            logger.info("Skipping already archived file: %s", source)
            return

        if existing.status == "PENDING":
            logger.info("Retrying pending archive %s: %s", existing.id, source)

            self._archive_pending_file(archive_id=existing.id, source=source, cloud_relpath=existing.cloud_relpath,
                                       size_bytes=existing.size_bytes, sha256_hash=sha256_hash)
            return

        logger.info("Skipping existing archive %s with status %s: %s", existing.id, existing.status, source)

    @staticmethod
    def _calculate_file_hash(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size_bytes = 0
        with path.open("rb") as file:
            while chunk := file.read(HASH_CHUNK_SIZE):
                digest.update(chunk)
                size_bytes += len(chunk)
        return size_bytes, digest.hexdigest()

    def _build_cloud_path(self, source: Path) -> str:
        try:
            relative_path = source.relative_to(self.vault_root)
        except ValueError as exc:
            raise WorkerError(f"File is outside VaultDrop root: {source}") from exc

        # rclone remote paths use POSIX separators.
        cloud_path = relative_path.as_posix()

        if not cloud_path or cloud_path == ".":
            raise WorkerError(f"Could not determine cloud path for: {source}")

        return cloud_path

    @staticmethod
    def _delete_source(source: Path) -> None:
        try:
            source.unlink()
        except FileNotFoundError:
            # Another process may already have removed it.
            logger.info("Source file already removed: %s", source)
        except OSError:
            # The cloud archive is already safe. Do not change its status
            # back to PENDING merely because local cleanup failed.
            logger.exception("Cloud archive succeeded but local deletion failed: %s", source)

    @staticmethod
    def _expected_remote_size(source: Path, plaintext_size: int) -> int | None:
        return None

    def _create_stub(self, *, archive_id: int, source: Path, cloud_relpath: str, sha256_hash: str) -> None:
        stub = source.with_name(source.name + STUB_SUFFIX)

        if stub.exists():
            raise WorkerError(f"SecureVault stub already exists: {stub}")

        content = ("SecureVault\n" f"archive_id={archive_id}\n" f"original_filename={source.name}\n"
                   f"sha256={sha256_hash}\n"  f"cloud_relpath={cloud_relpath}\n")

        try:
            stub.write_text(content, encoding="utf-8")
            source.unlink()
        except Exception:
            try:
                stub.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to clean up incomplete stub: %s", stub)
            raise
