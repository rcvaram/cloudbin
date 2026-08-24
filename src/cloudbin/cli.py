from __future__ import annotations

import argparse
from pathlib import Path

from cloudbin.config import (
    DEFAULT_CLOUDBIN_ROOT,
    CloudBinConfig,
    ConfigurationError,
)
from cloudbin.restore import (
    RestoreError,
    RestoreService,
)
from cloudbin.storage.database import Database
from cloudbin.storage.rclone import RcloneClient
from cloudbin.storage.rclone_config import (
    RcloneConfig,
    RcloneConfigError,
)
from cloudbin.watcher import VaultDropWatcher
from cloudbin.worker import WorkerService
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="cloudbin",
        description="Privacy-focused file archival.",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    subparsers.add_parser(
        "init",
        help="Initialize CloudBin.",
    )

    subparsers.add_parser(
        "start",
        help="Start CloudBin in the foreground.",
    )

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    restore_parser = subparsers.add_parser(
        "restore",
        help="Restore archived files and folders.",
    )

    restore_subparsers = restore_parser.add_subparsers(
        dest="restore_command",
        required=True,
    )

    restore_subparsers.add_parser(
        "list",
        help="List archived files available for restoration.",
    )

    restore_file_parser = restore_subparsers.add_parser(
        "file",
        help="Restore a single archived file.",
    )

    restore_file_parser.add_argument(
        "archive_id",
        type=int,
        help="Archive ID.",
    )

    restore_file_parser.add_argument(
        "--destination",
        type=Path,
        help="Destination path for the restored file.",
    )

    restore_file_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow an existing file to be overwritten.",
    )

    restore_folder_parser = restore_subparsers.add_parser(
        "folder",
        help="Restore all archived files in a folder.",
    )

    restore_folder_parser.add_argument(
        "folder",
        help="Folder path relative to VaultDrop.",
    )

    restore_folder_parser.add_argument(
        "--destination",
        type=Path,
        help="Parent directory for the restored folder.",
    )

    restore_folder_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow existing files to be overwritten.",
    )

    args = parser.parse_args()

    if args.command == "init":
        return _init()

    if args.command == "start":
        return _start()

    if args.command == "restore":
        return _restore(args)

    return 1


# ----------------------------------------------------------------------
# Init
# ----------------------------------------------------------------------


def _init() -> int:
    print()
    print("CloudBin Setup")
    print("==============")
    print()

    root_directory = _ask_path(
        "CloudBin directory",
        DEFAULT_CLOUDBIN_ROOT,
    )

    print()
    print("Discovering rclone remotes...")
    print()

    try:
        rclone_config = RcloneConfig()
        remotes = rclone_config.list_crypt_remotes()

    except RcloneConfigError as exc:
        print(f"Error: {exc}")
        return 1

    if not remotes:
        print("No rclone crypt remotes were found.")
        print()
        print("Create an rclone crypt remote first with:")
        print()
        print("    rclone config")
        print()
        return 1

    print("Available encrypted rclone remotes:")
    print()

    for index, remote in enumerate(
            remotes,
            start=1,
    ):
        print(f"  {index}. {remote.name}:")

    print()

    selected_remote = _select_remote(remotes)

    if selected_remote is None:
        print("Invalid selection.")
        return 1

    remote_name = f"{selected_remote.name}:"

    try:
        config = CloudBinConfig(
            root_directory=root_directory,
            rclone_remote=remote_name,
        )

        config.initialize_directories()
        config.save()
        config.set_active()

    except (
            ConfigurationError,
            OSError,
            ValueError,
    ) as exc:
        print(f"Error: {exc}")
        return 1

    print()
    print("CloudBin configuration")
    print("----------------------")
    print(f"CloudBin : {config.root_directory}")
    print(f"VaultDrop: {config.vault_root}")
    print(f"Restored : {config.restore_root}")
    print(f"Remote   : {config.rclone_remote}")
    print()
    print(
        f"Configuration saved to: "
        f"{config.config_path}"
    )
    print()
    print("CloudBin is now active.")
    print()
    print(
        "You can run 'cloudbin start' "
        "from any directory."
    )
    print()

    return 0


# ----------------------------------------------------------------------
# Start
# ----------------------------------------------------------------------


def _start() -> int:
    print()
    print("CloudBin")
    print("========")
    print()

    try:
        config = CloudBinConfig.load_active()

    except ConfigurationError as exc:
        print(f"Error: {exc}")
        print()
        print(
            "Run 'cloudbin init' to configure CloudBin."
        )
        return 1

    print(
        f"CloudBin  : {config.root_directory}"
    )
    print(
        f"VaultDrop : {config.vault_root}"
    )
    print(
        f"Restored  : {config.restore_root}"
    )
    print(
        f"Remote    : {config.rclone_remote}"
    )
    print()

    try:
        rclone_config = RcloneConfig()

        remote_name = (
            config.rclone_remote.removesuffix(":")
        )

        remote = rclone_config.require_crypt_remote(
            remote_name
        )

    except RcloneConfigError as exc:
        print(
            f"Storage validation failed: {exc}"
        )
        return 1

    print(
        f"✓ Encrypted remote validated: "
        f"{remote.name}:"
    )

    try:
        config.initialize_directories()

    except OSError as exc:
        print(
            f"Failed to prepare CloudBin directories: "
            f"{exc}"
        )
        return 1

    try:
        database = Database(
            config.database_path
        )

    except Exception as exc:
        print(
            f"Failed to initialize database: {exc}"
        )
        return 1

    print("✓ Database ready")

    try:
        storage = RcloneClient(
            remote=config.rclone_remote
        )

    except Exception as exc:
        print(
            f"Failed to initialize storage: {exc}"
        )
        return 1

    print("✓ Storage ready")

    worker = WorkerService(
        database=database,
        storage=storage,
        vault_root=config.vault_root,
    )

    try:
        watcher = VaultDropWatcher(
            watch_path=config.vault_root,
            worker=worker,
        )

    except (
            FileNotFoundError,
            ValueError,
    ) as exc:
        print(
            f"Failed to initialize watcher: {exc}"
        )
        return 1

    print("✓ Watcher ready")
    print()

    print("CloudBin is running.")
    print(
        f"Watching: {config.vault_root}"
    )
    print()
    print("Waiting for files...")
    print("Press Ctrl+C to stop.")
    print()

    try:
        watcher.run()

    except Exception as exc:
        print(
            f"CloudBin stopped unexpectedly: {exc}"
        )
        return 1

    print()
    print("CloudBin stopped.")

    return 0


# ----------------------------------------------------------------------
# Restore
# ----------------------------------------------------------------------


def _restore(args: argparse.Namespace) -> int:
    try:
        config = CloudBinConfig.load_active()

    except ConfigurationError as exc:
        print(f"Error: {exc}")
        print()
        print(
            "Run 'cloudbin init' to configure CloudBin."
        )
        return 1

    try:
        config.initialize_directories()

        database = Database(
            config.database_path
        )

        storage = RcloneClient(
            remote=config.rclone_remote
        )

        restore_service = RestoreService(
            database=database,
            storage=storage,
            vault_root=config.vault_root,
            restore_root=config.restore_root,
            temporary_root=config.temporary_root,
        )

    except (
            ConfigurationError,
            RestoreError,
            OSError,
            ValueError,
    ) as exc:
        print(f"Error: {exc}")
        return 1

    if args.restore_command == "list":
        return _restore_list(
            restore_service
        )

    if args.restore_command == "file":
        return _restore_file(
            restore_service,
            args,
        )

    if args.restore_command == "folder":
        return _restore_folder(
            restore_service,
            args,
        )

    return 1


def _restore_list(
        restore_service: RestoreService,
) -> int:
    try:
        archives = (
            restore_service.list_restorable_archives()
        )

    except RestoreError as exc:
        print(f"Restore failed: {exc}")
        return 1

    print()
    print("Restorable Archives")
    print("===================")
    print()

    if not archives:
        print("No archived files are available.")
        print()
        return 0

    print(
        f"{'ID':<6}"
        f"{'Filename':<45}"
        f"{'Size':>12}"
        f"  Path"
    )

    print("-" * 100)

    for archive in archives:
        print(
            f"{archive.id:<6}"
            f"{_truncate(archive.original_filename, 43):<45}"
            f"{archive.size_bytes:>12}"
            f"  {archive.cloud_relpath}"
        )

    print()

    return 0


def _restore_file(
        restore_service: RestoreService,
        args: argparse.Namespace,
) -> int:
    print()
    print(
        f"Restoring archive {args.archive_id}..."
    )

    try:
        restored_path = restore_service.restore_file(
            archive_id=args.archive_id,
            destination=args.destination,
            overwrite=args.overwrite,
        )

    except RestoreError as exc:
        print()
        print(f"Restore failed: {exc}")
        return 1

    print()
    print("✓ Restore completed.")
    print(
        f"Restored to: {restored_path}"
    )
    print()

    return 0


def _restore_folder(
        restore_service: RestoreService,
        args: argparse.Namespace,
) -> int:
    print()
    print(
        f"Restoring folder: {args.folder}"
    )

    try:
        restored_files = (
            restore_service.restore_folder(
                folder=args.folder,
                destination=args.destination,
                overwrite=args.overwrite,
            )
        )

    except RestoreError as exc:
        print()
        print(f"Restore failed: {exc}")
        return 1

    print()
    print(
        f"✓ Folder restore completed."
    )
    print(
        f"Files restored: {len(restored_files)}"
    )
    print()

    for path in restored_files:
        print(f"  ✓ {path}")

    print()

    return 0


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _truncate(
        value: str,
        maximum_length: int,
) -> str:
    if len(value) <= maximum_length:
        return value

    return value[: maximum_length - 3] + "..."


def _ask_path(
        label: str,
        default: Path,
) -> Path:
    value = input(
        f"{label} [{default}]: "
    ).strip()

    if not value:
        return default.resolve()

    return (
        Path(value)
        .expanduser()
        .resolve()
    )


def _select_remote(remotes):
    while True:
        value = input(
            f"Select remote [1-{len(remotes)}]: "
        ).strip()

        try:
            index = int(value)

        except ValueError:
            print("Please enter a number.")
            continue

        if not 1 <= index <= len(remotes):
            print("Invalid selection.")
            continue

        return remotes[index - 1]


if __name__ == "__main__":
    raise SystemExit(main())
