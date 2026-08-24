from pathlib import Path

import pytest

from cloudbin.config import (
    ACTIVE_CONFIG_PATH,
    CloudBinConfig,
    ConfigurationFileError,
    load_active_root,
    save_active_root,
)


def test_save_and_load(tmp_path: Path) -> None:
    root = tmp_path / "CloudBin"

    original = CloudBinConfig(
        root_directory=root,
        rclone_remote="my-cloudbin:",
    )

    original.save()

    loaded = CloudBinConfig.load(
        root / ".cloudbin" / "config.toml"
    )

    assert loaded == original


def test_paths_are_derived_from_root(tmp_path: Path) -> None:
    root = tmp_path / "CloudBin"

    config = CloudBinConfig(
        root_directory=root,
        rclone_remote="my-cloudbin:",
    )

    assert config.root_directory == root.resolve()

    assert config.internal_directory == (
        root / ".cloudbin"
    )

    assert config.config_path == (
        root
        / ".cloudbin"
        / "config.toml"
    )

    assert config.database_path == (
        root
        / ".cloudbin"
        / "cloudbin.sqlite"
    )

    assert config.vault_root == (
        root / "VaultDrop"
    )

    assert config.restore_root == (
        root / "Restored"
    )

    assert config.temporary_root == (
        root
        / ".cloudbin"
        / "tmp"
    )


def test_initialize_directories(tmp_path: Path) -> None:
    root = tmp_path / "CloudBin"

    config = CloudBinConfig(
        root_directory=root,
        rclone_remote="my-cloudbin:",
    )

    config.initialize_directories()

    assert config.internal_directory.is_dir()
    assert config.vault_root.is_dir()
    assert config.restore_root.is_dir()
    assert config.temporary_root.is_dir()


def test_load_expands_root_path(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"

    config_path.write_text(
        """
[cloudbin]
root = "~/CloudBin"

[storage]
rclone_remote = "my-cloudbin:"
""",
        encoding="utf-8",
    )

    config = CloudBinConfig.load(
        config_path
    )

    assert config.root_directory == (
        Path("~/CloudBin")
        .expanduser()
        .resolve()
    )

    assert config.vault_root == (
        Path("~/CloudBin")
        .expanduser()
        .resolve()
        / "VaultDrop"
    )

    assert config.database_path == (
        Path("~/CloudBin")
        .expanduser()
        .resolve()
        / ".cloudbin"
        / "cloudbin.sqlite"
    )

    assert config.rclone_remote == (
        "my-cloudbin:"
    )


def test_load_from_root(tmp_path: Path) -> None:
    root = tmp_path / "CloudBin"

    original = CloudBinConfig(
        root_directory=root,
        rclone_remote="my-cloudbin:",
    )

    original.initialize_directories()
    original.save()

    loaded = CloudBinConfig.from_root(
        root
    )

    assert loaded == original


def test_missing_config_fails(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ConfigurationFileError
    ):
        CloudBinConfig.load(
            tmp_path / "does-not-exist.toml"
        )


def test_invalid_toml_fails(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"

    config_path.write_text(
        "[cloudbin\ninvalid",
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigurationFileError
    ):
        CloudBinConfig.load(
            config_path
        )


def test_missing_required_field_fails(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"

    config_path.write_text(
        """
[cloudbin]

[storage]
""",
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigurationFileError
    ):
        CloudBinConfig.load(
            config_path
        )


def test_remote_must_end_with_colon(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError):
        CloudBinConfig(
            root_directory=tmp_path / "CloudBin",
            rclone_remote="my-cloudbin",
        )


# ----------------------------------------------------------------------
# Active CloudBin
# ----------------------------------------------------------------------


def test_save_and_load_active_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "CloudBin"

    active_path = (
        tmp_path
        / ".config"
        / "cloudbin"
        / "active.toml"
    )

    save_active_root(
        root,
        active_path,
    )

    loaded = load_active_root(
        active_path,
    )

    assert loaded == root.resolve()


def test_active_root_file_format(
    tmp_path: Path,
) -> None:
    root = tmp_path / "CloudBin"

    active_path = (
        tmp_path
        / "active.toml"
    )

    save_active_root(
        root,
        active_path,
    )

    content = active_path.read_text(
        encoding="utf-8"
    )

    assert content == (
        "[cloudbin]\n"
        f'root = "{root.resolve()}"\n'
    )


def test_missing_active_root_fails(
    tmp_path: Path,
) -> None:
    active_path = (
        tmp_path
        / "active.toml"
    )

    with pytest.raises(
        ConfigurationFileError
    ):
        load_active_root(
            active_path
        )


def test_invalid_active_root_toml_fails(
    tmp_path: Path,
) -> None:
    active_path = (
        tmp_path
        / "active.toml"
    )

    active_path.write_text(
        "[cloudbin\ninvalid",
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigurationFileError
    ):
        load_active_root(
            active_path
        )


def test_active_root_missing_root_fails(
    tmp_path: Path,
) -> None:
    active_path = (
        tmp_path
        / "active.toml"
    )

    active_path.write_text(
        """
[cloudbin]
""",
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigurationFileError
    ):
        load_active_root(
            active_path
        )


def test_config_set_active(
    tmp_path: Path,
) -> None:
    root = tmp_path / "CloudBin"

    active_path = (
        tmp_path
        / "active.toml"
    )

    config = CloudBinConfig(
        root_directory=root,
        rclone_remote="my-cloudbin:",
    )

    config.set_active()

    # The method uses the default global location,
    # so this test is intentionally not asserting
    # against a temporary active path.
    #
    # The dedicated save_active_root/load_active_root
    # tests above verify the actual behavior.