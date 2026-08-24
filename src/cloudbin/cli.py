from __future__ import annotations

import argparse
from pathlib import Path

from cloudbin.config import (
    DEFAULT_CONFIG_PATH,
    CloudBinConfig,
    ConfigurationError,
)
from cloudbin.storage.database import Database
from cloudbin.storage.rclone import RcloneClient
from cloudbin.storage.rclone_config import (
    RcloneConfig,
    RcloneConfigError,
)
from cloudbin.watcher import VaultDropWatcher
from cloudbin.worker import WorkerService

DEFAULT_VAULT_ROOT = (
    Path("~/CloudBin/VaultDrop").expanduser()
)

DEFAULT_DATA_DIRECTORY = (
    Path("~/.local/share/cloudbin").expanduser()
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
        help="Initialize CloudBin configuration.",
    )

    subparsers.add_parser(
        "start",
        help="Start CloudBin in the foreground.",
    )

    args = parser.parse_args()

    if args.command == "init":
        return _init()

    if args.command == "start":
        return _start()

    return 1


def _init() -> int:
    print()
    print("CloudBin Setup")
    print("==============")
    print()

    vault_root = _ask_path(
        "VaultDrop directory",
        DEFAULT_VAULT_ROOT,
    )

    data_directory = _ask_path(
        "CloudBin data directory",
        DEFAULT_DATA_DIRECTORY,
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

    remote_name = f"{selected_remote.name}:"

    config = CloudBinConfig(
        vault_root=vault_root,
        data_directory=data_directory,
        rclone_remote=remote_name,
    )

    print()
    print("CloudBin configuration")
    print("----------------------")
    print(f"VaultDrop : {config.vault_root}")
    print(f"Data      : {config.data_directory}")
    print(f"Remote    : {config.rclone_remote}")
    print()

    try:
        config.save()

    except ConfigurationError as exc:
        print(f"Error: {exc}")
        return 1

    print(
        "Configuration saved to: "
        f"{DEFAULT_CONFIG_PATH}"
    )

    return 0


def _start() -> int:
    print()
    print("CloudBin")
    print("========")
    print()

    # --------------------------------------------------------------
    # Load configuration
    # --------------------------------------------------------------

    try:
        config = CloudBinConfig.load()

    except ConfigurationError as exc:
        print(f"Error: {exc}")
        print()
        print("Run 'cloudbin init' to configure CloudBin.")
        return 1

    print(f"VaultDrop : {config.vault_root}")
    print(f"Data      : {config.data_directory}")
    print(f"Remote    : {config.rclone_remote}")
    print()

    # --------------------------------------------------------------
    # Validate configured rclone remote
    # --------------------------------------------------------------

    try:
        rclone_config = RcloneConfig()

        remote_name = config.rclone_remote.removesuffix(":")

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

    # --------------------------------------------------------------
    # Prepare application directories
    # --------------------------------------------------------------

    try:
        config.vault_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        config.data_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

    except OSError as exc:
        print(
            f"Failed to prepare directories: {exc}"
        )
        return 1

    # --------------------------------------------------------------
    # Initialize database
    # --------------------------------------------------------------

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

    # --------------------------------------------------------------
    # Initialize storage
    # --------------------------------------------------------------

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

    # --------------------------------------------------------------
    # Create worker
    # --------------------------------------------------------------

    worker = WorkerService(
        database=database,
        storage=storage,
        vault_root=config.vault_root,
    )

    # --------------------------------------------------------------
    # Create watcher
    # --------------------------------------------------------------

    try:
        watcher = VaultDropWatcher(
            watch_path=config.vault_root,
            worker=worker,
        )

    except (FileNotFoundError, ValueError) as exc:
        print(
            f"Failed to initialize watcher: {exc}"
        )
        return 1

    print("✓ Watcher ready")
    print()

    # --------------------------------------------------------------
    # Run in foreground
    # --------------------------------------------------------------

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
