from pathlib import Path

import pytest

from cloudbin.config import (
    CloudBinConfig,
    ConfigurationFileError,
)


def test_save_and_load(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"

    original = CloudBinConfig(
        vault_root=tmp_path / "VaultDrop",
        data_directory=tmp_path / "cloudbin-data",
        rclone_remote="my-cloudbin:",
    )

    original.save(config_path)

    loaded = CloudBinConfig.load(config_path)

    assert loaded == original


def test_database_path_is_derived(tmp_path: Path) -> None:
    config = CloudBinConfig(
        vault_root=tmp_path / "VaultDrop",
        data_directory=tmp_path / "cloudbin-data",
        rclone_remote="my-cloudbin:",
    )

    assert config.database_path == (
        tmp_path
        / "cloudbin-data"
        / "cloudbin.sqlite"
    )


def test_load_expands_paths(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"

    config_path.write_text(
        """
[vault]
root = "~/CloudBin/VaultDrop"

[data]
directory = "~/CloudBin"

[storage]
rclone_remote = "my-cloudbin:"
""",
        encoding="utf-8",
    )

    config = CloudBinConfig.load(config_path)

    assert config.vault_root == (
        Path("~/CloudBin/VaultDrop")
        .expanduser()
        .resolve()
    )

    assert config.data_directory == (
        Path("~/CloudBin")
        .expanduser()
        .resolve()
    )

    assert config.database_path == (
        Path("~/CloudBin")
        .expanduser()
        .resolve()
        / "cloudbin.sqlite"
    )

    assert config.rclone_remote == "my-cloudbin:"


def test_missing_config_fails(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationFileError):
        CloudBinConfig.load(
            tmp_path / "does-not-exist.toml"
        )


def test_invalid_toml_fails(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"

    config_path.write_text(
        "[vault\ninvalid",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationFileError):
        CloudBinConfig.load(config_path)


def test_missing_required_field_fails(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"

    config_path.write_text(
        """
[vault]
root = "/tmp/VaultDrop"

[data]
directory = "/tmp/cloudbin-data"

[storage]
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationFileError):
        CloudBinConfig.load(config_path)


def test_remote_must_end_with_colon(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        CloudBinConfig(
            vault_root=tmp_path / "VaultDrop",
            data_directory=tmp_path / "cloudbin-data",
            rclone_remote="my-cloudbin",
        )