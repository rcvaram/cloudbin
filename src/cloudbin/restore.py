from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path

from cloudbin.storage.database import ArchiveRecord, Database
from cloudbin.storage.rclone import RcloneClient

logger = logging.getLogger(__name__)

HASH_CHUNK_SIZE = 64 * 1024


class RestoreError(RuntimeError):
    """Base exception for CloudBin restore failures."""


class RestoreValidationError(RestoreError):
    """Raised when a restore request or archive is invalid."""


class RestoreIntegrityError(RestoreError):
    """Raised when restored data fails integrity verification."""


class RestoreDestinationError(RestoreError):
    """Raised when a restore destination is unsafe or invalid."""


class RestoreService:
    """
    Coordinates safe restoration of archived files and folders.

    Restore flow:

        encrypted cloud file
            -> temporary local file
            -> size verification
            -> SHA-256 verification
            -> atomic move
            -> restored destination

    The final destination is never used until the downloaded file has
    passed integrity verification.

    Folder restoration is composed from the same file restoration
    operation. A failure in one file does not invalidate files that
    were already restored successfully.
    """

    def __init__(
            self,
            database: Database,
            storage: RcloneClient,
            vault_root: str | Path,
            restore_root: str | Path,
            temporary_root: str | Path,
    ) -> None:
        self.database = database
        self.storage = storage

        self.vault_root = (
            Path(vault_root)
            .expanduser()
            .resolve()
        )

        self.restore_root = (
            Path(restore_root)
            .expanduser()
            .resolve()
        )

        self.temporary_root = (
            Path(temporary_root)
            .expanduser()
            .resolve()
        )

        self._validate_configuration()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _validate_configuration(self) -> None:
        if not self.vault_root.exists():
            raise RestoreValidationError(
                f"VaultDrop directory does not exist: {self.vault_root}"
            )

        if not self.vault_root.is_dir():
            raise RestoreValidationError(
                f"VaultDrop path is not a directory: {self.vault_root}"
            )

        if self._is_same_or_child(
                self.restore_root,
                self.vault_root,
        ):
            raise RestoreDestinationError(
                "Restore directory must not be inside VaultDrop: "
                f"{self.restore_root}"
            )

        if self._is_same_or_child(
                self.temporary_root,
                self.vault_root,
        ):
            raise RestoreDestinationError(
                "Restore temporary directory must not be inside "
                f"VaultDrop: {self.temporary_root}"
            )

    # ------------------------------------------------------------------
    # Archive discovery
    # ------------------------------------------------------------------

    def list_restorable_archives(self) -> list[ArchiveRecord]:
        """
        Return successfully archived records available for restoration.
        """
        return self.database.list_archives(
            status="ARCHIVED",
        )

    def get_archive(
            self,
            archive_id: int,
    ) -> ArchiveRecord:
        """
        Retrieve and validate an archive before restoration.
        """
        archive = self.database.get_archive(archive_id)

        self._validate_archive(archive)

        return archive

    # ------------------------------------------------------------------
    # File restore
    # ------------------------------------------------------------------

    def restore_file(
            self,
            archive_id: int,
            destination: str | Path | None = None,
            *,
            overwrite: bool = False,
    ) -> Path:
        """
        Restore a single archived file.

        If destination is omitted, the original relative path is
        reconstructed underneath the configured Restored directory.

        Example:

            VaultDrop:
                /home/user/CloudBin/VaultDrop

            Original:
                Documents/CV/cv.pdf

            Default restore:
                /home/user/CloudBin/Restored/Documents/CV/cv.pdf

        Returns:
            Final restored file path.
        """
        archive = self.get_archive(archive_id)

        final_destination = self._resolve_file_destination(
            archive,
            destination,
        )

        self._validate_final_destination(
            final_destination,
            overwrite=overwrite,
        )

        temporary_path: Path | None = None

        try:
            temporary_path = self._create_temporary_file(
                archive.original_filename,
            )

            logger.info(
                "Downloading archive %s: %s",
                archive.id,
                archive.cloud_relpath,
            )

            self.storage.download_from_drive(
                archive.cloud_relpath,
                temporary_path,
            )

            self._verify_downloaded_file(
                temporary_path,
                archive,
            )

            self._commit_file(
                temporary_path,
                final_destination,
                overwrite=overwrite,
            )

            temporary_path = None

            logger.info(
                "Restored archive %s to %s",
                archive.id,
                final_destination,
            )

            return final_destination

        except Exception:
            logger.exception(
                "Failed to restore archive %s",
                archive.id,
            )

            if temporary_path is not None:
                self._cleanup_temporary_file(
                    temporary_path,
                )

            raise

    # ------------------------------------------------------------------
    # Folder restore
    # ------------------------------------------------------------------

    def restore_folder(
            self,
            folder: str | Path,
            destination: str | Path | None = None,
            *,
            overwrite: bool = False,
    ) -> list[Path]:
        """
        Restore all archived files belonging to a folder.

        The folder path is relative to VaultDrop.

        Example:

            restore_folder("Documents")

        restores:

            Documents/CV/cv.pdf
            Documents/Invoices/invoice.pdf

        into:

            Restored/Documents/CV/cv.pdf
            Restored/Documents/Invoices/invoice.pdf
        """
        relative_folder = self._normalize_relative_path(
            folder,
        )

        archives = self._find_folder_archives(
            relative_folder,
        )

        if not archives:
            raise RestoreValidationError(
                f"No archived files found for folder: "
                f"{relative_folder}"
            )

        restore_base = self._resolve_folder_destination(
            relative_folder,
            destination,
        )

        self._validate_final_destination_root(
            restore_base,
        )

        restored: list[Path] = []
        failures: list[tuple[ArchiveRecord, Exception]] = []

        logger.info(
            "Restoring folder %s (%d files)",
            relative_folder,
            len(archives),
        )

        for archive in archives:
            try:
                final_destination = (
                    self._destination_for_folder_file(
                        archive,
                        relative_folder,
                        restore_base,
                    )
                )

                restored_path = self.restore_file(
                    archive.id,
                    destination=final_destination,
                    overwrite=overwrite,
                )

                restored.append(restored_path)

            except Exception as exc:
                failures.append(
                    (archive, exc),
                )

        if failures:
            message = self._build_folder_failure_message(
                relative_folder,
                restored,
                failures,
            )

            raise RestoreError(message)

        logger.info(
            "Folder restore completed: %s (%d files)",
            relative_folder,
            len(restored),
        )

        return restored

    # ------------------------------------------------------------------
    # Archive validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_archive(
            archive: ArchiveRecord,
    ) -> None:
        if archive.status != "ARCHIVED":
            raise RestoreValidationError(
                f"Archive {archive.id} cannot be restored. "
                f"Current status: {archive.status}"
            )

        if not archive.cloud_relpath:
            raise RestoreValidationError(
                f"Archive {archive.id} has no cloud path."
            )

        if not archive.original_abspath:
            raise RestoreValidationError(
                f"Archive {archive.id} has no original path."
            )

        if not archive.original_filename:
            raise RestoreValidationError(
                f"Archive {archive.id} has no original filename."
            )

        if archive.size_bytes < 0:
            raise RestoreValidationError(
                f"Archive {archive.id} has an invalid file size."
            )

        if not archive.sha256_hash:
            raise RestoreValidationError(
                f"Archive {archive.id} has no SHA-256 hash."
            )

    # ------------------------------------------------------------------
    # Destination resolution
    # ------------------------------------------------------------------

    def _resolve_file_destination(
            self,
            archive: ArchiveRecord,
            destination: str | Path | None,
    ) -> Path:
        if destination is not None:
            return (
                Path(destination)
                .expanduser()
                .resolve()
            )

        relative_path = self._original_relative_path(
            archive,
        )

        return self.restore_root / relative_path

    def _resolve_folder_destination(
            self,
            relative_folder: str,
            destination: str | Path | None,
    ) -> Path:
        if destination is None:
            return self.restore_root / relative_folder

        return (
                Path(destination)
                .expanduser()
                .resolve()
                / relative_folder
        )

    def _destination_for_folder_file(
            self,
            archive: ArchiveRecord,
            folder: str,
            restore_base: Path,
    ) -> Path:
        relative_file = self._original_relative_path(
            archive,
        )

        folder_path = Path(folder)

        try:
            relative_inside_folder = (
                relative_file.relative_to(folder_path)
            )
        except ValueError as exc:
            raise RestoreValidationError(
                f"Archive path '{relative_file}' is not "
                f"inside folder '{folder}'."
            ) from exc

        return restore_base / relative_inside_folder

    # ------------------------------------------------------------------
    # Destination safety
    # ------------------------------------------------------------------

    def _validate_final_destination(
            self,
            destination: Path,
            *,
            overwrite: bool,
    ) -> None:
        self._validate_final_destination_root(
            destination.parent,
        )

        if not destination.exists():
            return

        if destination.is_dir():
            raise RestoreDestinationError(
                f"Restore destination is a directory: "
                f"{destination}"
            )

        if not overwrite:
            raise RestoreDestinationError(
                f"File already exists: {destination}. "
                "Refusing to overwrite it."
            )

    def _validate_final_destination_root(
            self,
            destination: Path,
    ) -> None:
        resolved = destination.resolve()

        if self._is_same_or_child(
                resolved,
                self.vault_root,
        ):
            raise RestoreDestinationError(
                "CloudBin will not restore files inside VaultDrop. "
                f"Destination: {resolved}"
            )

    # ------------------------------------------------------------------
    # Temporary file handling
    # ------------------------------------------------------------------

    def _create_temporary_file(
            self,
            original_filename: str,
    ) -> Path:
        self.temporary_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        safe_name = Path(original_filename).name

        fd, path = tempfile.mkstemp(
            prefix=".restore-",
            suffix=f"-{safe_name}",
            dir=self.temporary_root,
        )

        os.close(fd)

        return Path(path)

    @staticmethod
    def _cleanup_temporary_file(
            path: Path,
    ) -> None:
        try:
            path.unlink(
                missing_ok=True,
            )
        except OSError:
            logger.exception(
                "Could not remove temporary restore file: %s",
                path,
            )

    # ------------------------------------------------------------------
    # Integrity verification
    # ------------------------------------------------------------------

    def _verify_downloaded_file(
            self,
            temporary_path: Path,
            archive: ArchiveRecord,
    ) -> None:
        if not temporary_path.exists():
            raise RestoreError(
                "Downloaded restore file does not exist: "
                f"{temporary_path}"
            )

        if not temporary_path.is_file():
            raise RestoreError(
                "Downloaded restore path is not a file: "
                f"{temporary_path}"
            )

        actual_size = temporary_path.stat().st_size

        if actual_size != archive.size_bytes:
            raise RestoreIntegrityError(
                f"File size verification failed for archive "
                f"{archive.id}: "
                f"expected {archive.size_bytes}, "
                f"got {actual_size}."
            )

        actual_hash = self._calculate_sha256(
            temporary_path,
        )

        if actual_hash != archive.sha256_hash:
            raise RestoreIntegrityError(
                f"SHA-256 verification failed for archive "
                f"{archive.id}: "
                f"expected {archive.sha256_hash}, "
                f"got {actual_hash}."
            )

    @staticmethod
    def _calculate_sha256(
            path: Path,
    ) -> str:
        digest = hashlib.sha256()

        with path.open("rb") as file:
            while chunk := file.read(HASH_CHUNK_SIZE):
                digest.update(chunk)

        return digest.hexdigest()

    # ------------------------------------------------------------------
    # Atomic commit
    # ------------------------------------------------------------------

    def _commit_file(
            self,
            temporary_path: Path,
            final_destination: Path,
            *,
            overwrite: bool,
    ) -> None:
        final_destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if final_destination.exists():
            if not overwrite:
                raise RestoreDestinationError(
                    f"File already exists: "
                    f"{final_destination}"
                )

            if final_destination.is_dir():
                raise RestoreDestinationError(
                    f"Cannot overwrite directory: "
                    f"{final_destination}"
                )

            final_destination.unlink()

        temporary_path.replace(
            final_destination,
        )

    # ------------------------------------------------------------------
    # Folder discovery
    # ------------------------------------------------------------------

    def _find_folder_archives(
            self,
            relative_folder: str,
    ) -> list[ArchiveRecord]:
        prefix = relative_folder.rstrip("/") + "/"

        archives = self.database.list_archives(
            status="ARCHIVED",
        )

        matches: list[ArchiveRecord] = []

        for archive in archives:
            relative_path = (
                self._original_relative_path(archive)
                .as_posix()
            )

            if relative_path.startswith(prefix):
                matches.append(archive)

        matches.sort(
            key=lambda archive: (
                archive.original_abspath.lower()
            ),
        )

        return matches

    # ------------------------------------------------------------------
    # Path handling
    # ------------------------------------------------------------------

    def _original_relative_path(
            self,
            archive: ArchiveRecord,
    ) -> Path:
        original = (
            Path(archive.original_abspath)
            .expanduser()
            .resolve()
        )

        try:
            relative = original.relative_to(
                self.vault_root,
            )
        except ValueError as exc:
            raise RestoreValidationError(
                f"Archive {archive.id} points outside "
                f"VaultDrop: {original}"
            ) from exc

        if not relative.parts:
            raise RestoreValidationError(
                f"Archive {archive.id} has an invalid "
                "original path."
            )

        return relative

    @staticmethod
    def _normalize_relative_path(
            path: str | Path,
    ) -> str:
        value = (
            str(path)
            .replace("\\", "/")
            .strip()
        )

        if not value:
            raise RestoreValidationError(
                "Restore path cannot be empty."
            )

        candidate = Path(value)

        if candidate.is_absolute():
            raise RestoreValidationError(
                "Restore path must be relative to VaultDrop."
            )

        parts = [
            part
            for part in value.split("/")
            if part not in ("", ".")
        ]

        if not parts:
            raise RestoreValidationError(
                "Restore path is invalid."
            )

        if any(part == ".." for part in parts):
            raise RestoreValidationError(
                "Restore path must not contain '..'."
            )

        return "/".join(parts)

    @staticmethod
    def _is_same_or_child(
            path: Path,
            root: Path,
    ) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    # ------------------------------------------------------------------
    # Folder error reporting
    # ------------------------------------------------------------------

    @staticmethod
    def _build_folder_failure_message(
            folder: str,
            restored: list[Path],
            failures: list[tuple[ArchiveRecord, Exception]],
    ) -> str:
        lines = [
            f"Folder restore completed with errors: {folder}",
            "",
            f"Successfully restored: {len(restored)}",
            f"Failed:              {len(failures)}",
            "",
            "Failed files:",
        ]

        for archive, error in failures:
            lines.append(
                f"  - {archive.original_abspath}: {error}"
            )

        return "\n".join(lines)
