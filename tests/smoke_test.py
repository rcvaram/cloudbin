from __future__ import annotations

from pathlib import Path

from cloudbin.storage.database import Database
from cloudbin.storage.rclone import RcloneClient
from cloudbin.watcher import VaultDropWatcher
from cloudbin.worker import VaultWorker


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    test_root = project_root / "test-data" / "PackageSmokeTest"
    database_path = project_root / "data" / "smoke-test.sqlite"

    test_root.mkdir(parents=True, exist_ok=True)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    print("1. Testing CloudBin imports...")
    print("   OK")

    print("2. Initializing database...")
    database = Database(str(database_path))
    print("   OK")

    print("3. Initializing rclone client...")
    storage = RcloneClient(remote="gdrive-crypt:")
    print("   OK")

    print("4. Creating worker...")
    worker = VaultWorker(
        database=database,
        storage=storage,
        vault_root=test_root,
    )
    print("   OK")

    print("5. Creating filesystem watcher...")
    watcher = VaultDropWatcher(
        watch_path=test_root,
        worker=worker,
    )
    print("   OK")

    print()
    print("CloudBin package smoke test PASSED.")


if __name__ == "__main__":
    main()