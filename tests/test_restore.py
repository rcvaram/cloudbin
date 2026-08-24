from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from cloudbin.restore import (
    RestoreDestinationError,
    RestoreError,
    RestoreIntegrityError,
    RestoreService,
    RestoreValidationError,
)
from cloudbin.storage.database import Database


# ---------------------------------------------------------------------------
# Fake storage
# ---------------------------------------------------------------------------


class FakeRcloneClient:
    """
    Small in-memory replacement for RcloneClient.

    It simulates the cloud by copying files from a local directory.

    This lets us test RestoreService without requiring a real rclone
    installation or a real cloud account.
    """

    def __init__(self, cloud_root: Path) -> None:
        self.cloud_root = cloud_root
        self.cloud_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.download_calls: list[tuple[str, Path]] = []

        self.fail_download = False
        self.corrupt_download = False

    def download_from_drive(
        self,
        cloud_relpath: str,
        local_path: str | Path,
    ) -> None:
        self.download_calls.append(
            (
                cloud_relpath,
                Path(local_path),
            )
        )

        if self.fail_download:
            raise RuntimeError(
                "Simulated rclone download failure."
            )

        source = (
            self.cloud_root
            / cloud_relpath
        )

        if not source.exists():
            raise RuntimeError(
                f"Simulated remote file does not exist: {cloud_relpath}"
            )

        destination = Path(local_path)
        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        shutil.copy2(
            source,
            destination,
        )

        if self.corrupt_download:
            with destination.open("ab") as file:
                file.write(b"CORRUPTED")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while chunk := file.read(64 * 1024):
            digest.update(chunk)

    return digest.hexdigest()


def create_archive(
    database: Database,
    *,
    vault_root: Path,
    relative_path: str,
    cloud_relpath: str | None = None,
    status: str = "ARCHIVED",
) -> int:
    source = (
        vault_root
        / relative_path
    )

    source.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    source.write_bytes(
        f"test data: {relative_path}".encode()
    )

    return database.create_archive(
        original_filename=source.name,
        original_abspath=str(source.resolve()),
        cloud_relpath=(
            cloud_relpath
            if cloud_relpath is not None
            else relative_path
        ),
        size_bytes=source.stat().st_size,
        sha256_hash=sha256(source),
        status=status,
        source_type="VAULTDROP",
    )


def copy_to_fake_cloud(
    vault_root: Path,
    cloud_root: Path,
    relative_path: str,
    cloud_relpath: str | None = None,
) -> None:
    source = vault_root / relative_path

    remote_path = (
        cloud_root
        / (
            cloud_relpath
            if cloud_relpath is not None
            else relative_path
        )
    )

    remote_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    shutil.copy2(
        source,
        remote_path,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def environment(tmp_path: Path):
    vault_root = tmp_path / "CloudBin" / "VaultDrop"
    restore_root = tmp_path / "CloudBin" / "Restored"
    temporary_root = (
        tmp_path
        / "CloudBin"
        / ".cloudbin"
        / "tmp"
    )

    database_path = (
        tmp_path
        / "CloudBin"
        / ".cloudbin"
        / "cloudbin.sqlite"
    )

    cloud_root = (
        tmp_path
        / "fake-cloud"
    )

    vault_root.mkdir(
        parents=True,
    )

    restore_root.mkdir(
        parents=True,
    )

    temporary_root.mkdir(
        parents=True,
    )

    database = Database(
        database_path,
    )

    storage = FakeRcloneClient(
        cloud_root,
    )

    service = RestoreService(
        database=database,
        storage=storage,
        vault_root=vault_root,
        restore_root=restore_root,
        temporary_root=temporary_root,
    )

    return {
        "vault": vault_root,
        "restore": restore_root,
        "temporary": temporary_root,
        "cloud": cloud_root,
        "database": database,
        "storage": storage,
        "service": service,
    }


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


def test_restore_requires_existing_vault(environment) -> None:
    vault = environment["vault"]
    vault.rmdir()

    with pytest.raises(RestoreValidationError):
        RestoreService(
            database=environment["database"],
            storage=environment["storage"],
            vault_root=vault,
            restore_root=environment["restore"],
            temporary_root=environment["temporary"],
        )


def test_restore_directory_cannot_be_inside_vault(environment) -> None:
    vault = environment["vault"]

    unsafe_restore = vault / "Restored"

    with pytest.raises(RestoreDestinationError):
        RestoreService(
            database=environment["database"],
            storage=environment["storage"],
            vault_root=vault,
            restore_root=unsafe_restore,
            temporary_root=environment["temporary"],
        )


def test_temporary_directory_cannot_be_inside_vault(environment) -> None:
    vault = environment["vault"]

    unsafe_temporary = vault / ".tmp"

    with pytest.raises(RestoreDestinationError):
        RestoreService(
            database=environment["database"],
            storage=environment["storage"],
            vault_root=vault,
            restore_root=environment["restore"],
            temporary_root=unsafe_temporary,
        )


# ---------------------------------------------------------------------------
# Archive discovery
# ---------------------------------------------------------------------------


def test_list_restorable_archives_returns_only_archived(
    environment,
) -> None:
    database = environment["database"]
    vault = environment["vault"]
    service = environment["service"]

    create_archive(
        database,
        vault_root=vault,
        relative_path="one.txt",
        status="ARCHIVED",
    )

    create_archive(
        database,
        vault_root=vault,
        relative_path="two.txt",
        status="PENDING",
    )

    archives = service.list_restorable_archives()

    assert len(archives) == 1
    assert archives[0].original_filename == "one.txt"


def test_get_archive_returns_archive(environment) -> None:
    database = environment["database"]
    vault = environment["vault"]
    service = environment["service"]

    archive_id = create_archive(
        database,
        vault_root=vault,
        relative_path="document.pdf",
    )

    archive = service.get_archive(
        archive_id,
    )

    assert archive.id == archive_id
    assert archive.original_filename == "document.pdf"
    assert archive.status == "ARCHIVED"


def test_pending_archive_cannot_be_restored(environment) -> None:
    database = environment["database"]
    vault = environment["vault"]
    service = environment["service"]

    archive_id = create_archive(
        database,
        vault_root=vault,
        relative_path="pending.txt",
        status="PENDING",
    )

    with pytest.raises(RestoreValidationError):
        service.restore_file(
            archive_id,
        )


# ---------------------------------------------------------------------------
# Single file restore
# ---------------------------------------------------------------------------


def test_restore_file_successfully(
    environment,
) -> None:
    database = environment["database"]
    vault = environment["vault"]
    cloud = environment["cloud"]
    restore = environment["restore"]
    service = environment["service"]

    relative_path = "Documents/report.txt"

    archive_id = create_archive(
        database,
        vault_root=vault,
        relative_path=relative_path,
    )

    copy_to_fake_cloud(
        vault,
        cloud,
        relative_path,
    )

    # Simulate the original file being deleted after archival.
    (vault / relative_path).unlink()

    restored = service.restore_file(
        archive_id,
    )

    expected = (
        restore
        / "Documents"
        / "report.txt"
    )

    assert restored == expected
    assert restored.exists()
    assert restored.read_text() == (
        "test data: Documents/report.txt"
    )


def test_restore_preserves_sha256(
    environment,
) -> None:
    database = environment["database"]
    vault = environment["vault"]
    cloud = environment["cloud"]
    service = environment["service"]

    relative_path = "Documents/report.txt"

    archive_id = create_archive(
        database,
        vault_root=vault,
        relative_path=relative_path,
    )

    original = vault / relative_path
    expected_hash = sha256(original)

    copy_to_fake_cloud(
        vault,
        cloud,
        relative_path,
    )

    original.unlink()

    restored = service.restore_file(
        archive_id,
    )

    assert sha256(restored) == expected_hash


def test_archive_remains_archived_after_restore(
    environment,
) -> None:
    database = environment["database"]
    vault = environment["vault"]
    cloud = environment["cloud"]
    service = environment["service"]

    relative_path = "document.txt"

    archive_id = create_archive(
        database,
        vault_root=vault,
        relative_path=relative_path,
    )

    copy_to_fake_cloud(
        vault,
        cloud,
        relative_path,
    )

    (vault / relative_path).unlink()

    service.restore_file(
        archive_id,
    )

    archive = database.get_archive(
        archive_id,
    )

    assert archive.status == "ARCHIVED"


# ---------------------------------------------------------------------------
# Explicit destination
# ---------------------------------------------------------------------------


def test_restore_file_to_explicit_destination(
    environment,
) -> None:
    database = environment["database"]
    vault = environment["vault"]
    cloud = environment["cloud"]
    service = environment["service"]

    relative_path = "document.txt"

    archive_id = create_archive(
        database,
        vault_root=vault,
        relative_path=relative_path,
    )

    copy_to_fake_cloud(
        vault,
        cloud,
        relative_path,
    )

    (vault / relative_path).unlink()

    destination = (
        environment["restore"]
        / "custom"
        / "my-document.txt"
    )

    restored = service.restore_file(
        archive_id,
        destination=destination,
    )

    assert restored == destination
    assert destination.exists()


# ---------------------------------------------------------------------------
# Existing destination
# ---------------------------------------------------------------------------


def test_existing_destination_is_not_overwritten(
    environment,
) -> None:
    database = environment["database"]
    vault = environment["vault"]
    cloud = environment["cloud"]
    restore = environment["restore"]
    service = environment["service"]

    relative_path = "document.txt"

    archive_id = create_archive(
        database,
        vault_root=vault,
        relative_path=relative_path,
    )

    copy_to_fake_cloud(
        vault,
        cloud,
        relative_path,
    )

    (vault / relative_path).unlink()

    destination = restore / relative_path

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination.write_text(
        "DO NOT OVERWRITE",
    )

    with pytest.raises(RestoreDestinationError):
        service.restore_file(
            archive_id,
        )

    assert destination.read_text() == (
        "DO NOT OVERWRITE"
    )


def test_existing_destination_can_be_overwritten(
    environment,
) -> None:
    database = environment["database"]
    vault = environment["vault"]
    cloud = environment["cloud"]
    restore = environment["restore"]
    service = environment["service"]

    relative_path = "document.txt"

    archive_id = create_archive(
        database,
        vault_root=vault,
        relative_path=relative_path,
    )

    copy_to_fake_cloud(
        vault,
        cloud,
        relative_path,
    )

    (vault / relative_path).unlink()

    destination = restore / relative_path

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination.write_text(
        "OLD CONTENT",
    )

    restored = service.restore_file(
        archive_id,
        overwrite=True,
    )

    assert restored == destination

    assert destination.read_text() == (
        "test data: document.txt"
    )


# ---------------------------------------------------------------------------
# VaultDrop protection
# ---------------------------------------------------------------------------


def test_restore_cannot_write_inside_vault(
    environment,
) -> None:
    database = environment["database"]
    vault = environment["vault"]
    cloud = environment["cloud"]
    service = environment["service"]

    relative_path = "document.txt"

    archive_id = create_archive(
        database,
        vault_root=vault,
        relative_path=relative_path,
    )

    copy_to_fake_cloud(
        vault,
        cloud,
        relative_path,
    )

    destination = (
        vault
        / "Restored"
        / "document.txt"
    )

    with pytest.raises(RestoreDestinationError):
        service.restore_file(
            archive_id,
            destination=destination,
        )

    assert not destination.exists()


# ---------------------------------------------------------------------------
# Integrity verification
# ---------------------------------------------------------------------------


def test_corrupted_download_is_rejected(
    environment,
) -> None:
    database = environment["database"]
    vault = environment["vault"]
    storage = environment["storage"]
    cloud = environment["cloud"]
    temporary = environment["temporary"]
    service = environment["service"]

    relative_path = "document.txt"

    archive_id = create_archive(
        database,
        vault_root=vault,
        relative_path=relative_path,
    )

    copy_to_fake_cloud(
        vault,
        cloud,
        relative_path,
    )

    (vault / relative_path).unlink()

    storage.corrupt_download = True

    with pytest.raises(RestoreIntegrityError):
        service.restore_file(
            archive_id,
        )

    assert list(temporary.iterdir()) == []

    restored = (
        environment["restore"]
        / relative_path
    )

    assert not restored.exists()


def test_download_failure_does_not_create_final_file(
    environment,
) -> None:
    database = environment["database"]
    vault = environment["vault"]
    storage = environment["storage"]
    service = environment["service"]

    relative_path = "document.txt"

    archive_id = create_archive(
        database,
        vault_root=vault,
        relative_path=relative_path,
    )

    (vault / relative_path).unlink()

    storage.fail_download = True

    with pytest.raises(RuntimeError):
        service.restore_file(
            archive_id,
        )

    restored = (
        environment["restore"]
        / relative_path
    )

    assert not restored.exists()


def test_temporary_file_is_removed_after_failure(
    environment,
) -> None:
    database = environment["database"]
    vault = environment["vault"]
    storage = environment["storage"]
    temporary = environment["temporary"]
    service = environment["service"]

    relative_path = "document.txt"

    archive_id = create_archive(
        database,
        vault_root=vault,
        relative_path=relative_path,
    )

    (vault / relative_path).unlink()

    storage.fail_download = True

    with pytest.raises(RuntimeError):
        service.restore_file(
            archive_id,
        )

    assert list(temporary.iterdir()) == []


# ---------------------------------------------------------------------------
# Folder restore
# ---------------------------------------------------------------------------


def test_restore_folder(
    environment,
) -> None:
    database = environment["database"]
    vault = environment["vault"]
    cloud = environment["cloud"]
    restore = environment["restore"]
    service = environment["service"]

    files = [
        "Documents/CV/cv.pdf",
        "Documents/CV/cover-letter.pdf",
        "Documents/Invoices/invoice-01.pdf",
        "Documents/Invoices/invoice-02.pdf",
        "Photos/photo.jpg",
    ]

    archive_ids = []

    for relative_path in files:
        archive_id = create_archive(
            database,
            vault_root=vault,
            relative_path=relative_path,
        )

        copy_to_fake_cloud(
            vault,
            cloud,
            relative_path,
        )

        archive_ids.append(
            archive_id
        )

        (vault / relative_path).unlink()

    restored = service.restore_folder(
        "Documents",
    )

    assert len(restored) == 4

    expected_files = {
        restore / "Documents/CV/cv.pdf",
        restore / "Documents/CV/cover-letter.pdf",
        restore / "Documents/Invoices/invoice-01.pdf",
        restore / "Documents/Invoices/invoice-02.pdf",
    }

    assert set(restored) == expected_files

    assert not (
        restore
        / "Photos/photo.jpg"
    ).exists()


def test_restore_folder_preserves_content(
    environment,
) -> None:
    database = environment["database"]
    vault = environment["vault"]
    cloud = environment["cloud"]
    restore = environment["restore"]
    service = environment["service"]

    relative_path = "Documents/CV/cv.pdf"

    archive_id = create_archive(
        database,
        vault_root=vault,
        relative_path=relative_path,
    )

    expected_hash = sha256(
        vault / relative_path
    )

    copy_to_fake_cloud(
        vault,
        cloud,
        relative_path,
    )

    (vault / relative_path).unlink()

    service.restore_folder(
        "Documents",
    )

    restored = (
        restore
        / "Documents"
        / "CV"
        / "cv.pdf"
    )

    assert restored.exists()
    assert sha256(restored) == expected_hash


def test_restore_folder_with_explicit_parent(
    environment,
) -> None:
    database = environment["database"]
    vault = environment["vault"]
    cloud = environment["cloud"]
    service = environment["service"]

    relative_path = "Documents/CV/cv.pdf"

    create_archive(
        database,
        vault_root=vault,
        relative_path=relative_path,
    )

    copy_to_fake_cloud(
        vault,
        cloud,
        relative_path,
    )

    (vault / relative_path).unlink()

    parent = (
        environment["restore"]
        / "MyRestores"
    )

    service.restore_folder(
        "Documents",
        destination=parent,
    )

    expected = (
        parent
        / "Documents"
        / "CV"
        / "cv.pdf"
    )

    assert expected.exists()


def test_restore_nonexistent_folder_fails(
    environment,
) -> None:
    with pytest.raises(RestoreValidationError):
        environment["service"].restore_folder(
            "DoesNotExist",
        )


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "",
        ".",
        "..",
        "../Documents",
        "Documents/../../Secrets",
        "/etc/passwd",
    ],
)
def test_invalid_restore_folder_paths_are_rejected(
    environment,
    path: str,
) -> None:
    with pytest.raises(RestoreValidationError):
        environment["service"].restore_folder(
            path,
        )