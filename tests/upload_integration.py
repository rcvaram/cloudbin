from __future__ import annotations

import hashlib
from pathlib import Path

from cloudbin.storage.database import Database
from cloudbin.storage.rclone import RcloneClient
from cloudbin.worker import VaultWorker


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent

    test_root = project_root / "test-data" / "PackageUploadTest"
    database_path = project_root / "data" / "test-package-upload.sqlite"

    test_root.mkdir(parents=True, exist_ok=True)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    source = test_root / "cloudbin-upload-test.txt"
    content = b"CloudBin real upload integration test.\n"

    source.write_bytes(content)

    expected_hash = hashlib.sha256(content).hexdigest()
    expected_size = len(content)

    print("=== CloudBin Upload Integration Test ===")
    print(f"Source: {source}")
    print(f"Expected size: {expected_size}")
    print(f"Expected SHA-256: {expected_hash}")

    # ---------------------------------------------------------
    # Initialize real components
    # ---------------------------------------------------------

    print("\n[1] Initializing database...")
    database = Database(str(database_path))
    print("    OK")

    print("[2] Initializing rclone...")
    storage = RcloneClient(remote="gdrive-crypt:")
    print("    OK")

    print("[3] Initializing worker...")
    worker = VaultWorker(
        database=database,
        storage=storage,
        vault_root=test_root,
    )
    print("    OK")

    # ---------------------------------------------------------
    # Real archive operation
    # ---------------------------------------------------------

    print("\n[4] Uploading file through VaultWorker...")

    worker.process_file(source)

    print("    Worker completed")

    # ---------------------------------------------------------
    # Verify local file was removed
    # ---------------------------------------------------------

    print("\n[5] Checking local source...")

    if source.exists():
        raise AssertionError(
            f"Source file still exists after successful archive: {source}"
        )

    print("    OK - local source removed")

    # ---------------------------------------------------------
    # Verify database
    # ---------------------------------------------------------

    print("\n[6] Checking database...")

    archives = database.list_archives()

    matching = [
        archive
        for archive in archives
        if archive.original_filename == source.name
    ]

    if len(matching) != 1:
        raise AssertionError(
            f"Expected exactly one archive record, found {len(matching)}"
        )

    archive = matching[0]

    if archive.status != "ARCHIVED":
        raise AssertionError(
            f"Expected ARCHIVED status, got {archive.status}"
        )

    if archive.sha256_hash != expected_hash:
        raise AssertionError(
            f"Hash mismatch: "
            f"expected {expected_hash}, "
            f"got {archive.sha256_hash}"
        )

    if archive.size_bytes != expected_size:
        raise AssertionError(
            f"Size mismatch: "
            f"expected {expected_size}, "
            f"got {archive.size_bytes}"
        )

    print(f"    Status: {archive.status}")
    print(f"    SHA-256: {archive.sha256_hash}")
    print(f"    Size: {archive.size_bytes}")
    print(f"    Cloud path: {archive.cloud_relpath}")

    # ---------------------------------------------------------
    # Verify real cloud object
    # ---------------------------------------------------------

    print("\n[7] Checking encrypted cloud archive...")

    metadata = storage.verify_cloud_file(
        archive.cloud_relpath,
        expected_size=expected_size,
    )

    print("    Remote file exists")
    print(f"    Remote metadata: {metadata}")

    print("\n========================================")
    print("CloudBin upload integration test PASSED")
    print("========================================")


if __name__ == "__main__":
    main()
