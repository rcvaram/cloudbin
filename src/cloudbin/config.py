from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = (
    Path("~/.config/cloudbin/config.toml").expanduser()
)

DATABASE_FILENAME = "cloudbin.sqlite"


class ConfigurationError(RuntimeError):
    """Base exception for CloudBin configuration errors."""


class ConfigurationFileError(ConfigurationError):
    """Raised when the configuration file cannot be read or parsed."""


@dataclass(frozen=True)
class CloudBinConfig:
    """
    CloudBin application configuration.

    The data directory is controlled by the user, while the actual
    SQLite database filename is an implementation detail of CloudBin.
    """

    vault_root: Path
    data_directory: Path
    rclone_remote: str

    def __post_init__(self) -> None:
        if not self.rclone_remote:
            raise ValueError(
                "rclone_remote cannot be empty."
            )

        if not self.rclone_remote.endswith(":"):
            raise ValueError(
                "rclone_remote must end with ':'. "
                "Example: 'my-cloudbin:'"
            )

    @property
    def database_path(self) -> Path:
        """
        Return the path to CloudBin's SQLite database.
        """

        return self.data_directory / DATABASE_FILENAME

    @classmethod
    def load(
        cls,
        path: str | Path = DEFAULT_CONFIG_PATH,
    ) -> CloudBinConfig:
        config_path = (
            Path(path)
            .expanduser()
            .resolve()
        )

        if not config_path.exists():
            raise ConfigurationFileError(
                "CloudBin configuration file does not exist: "
                f"{config_path}"
            )

        if not config_path.is_file():
            raise ConfigurationFileError(
                "CloudBin configuration path is not a file: "
                f"{config_path}"
            )

        try:
            with config_path.open("rb") as file:
                data = tomllib.load(file)

        except OSError as exc:
            raise ConfigurationFileError(
                "Could not read CloudBin configuration: "
                f"{config_path}"
            ) from exc

        except tomllib.TOMLDecodeError as exc:
            raise ConfigurationFileError(
                "Invalid TOML configuration: "
                f"{config_path}"
            ) from exc

        return cls._from_data(data)

    @classmethod
    def _from_data(
        cls,
        data: dict,
    ) -> CloudBinConfig:
        try:
            vault = data["vault"]
            data_config = data["data"]
            storage = data["storage"]

            vault_root = vault["root"]
            data_directory = data_config["directory"]
            rclone_remote = storage["rclone_remote"]

        except (KeyError, TypeError) as exc:
            raise ConfigurationFileError(
                "CloudBin configuration is missing "
                "required fields."
            ) from exc

        if not isinstance(vault_root, str):
            raise ConfigurationFileError(
                "vault.root must be a string."
            )

        if not isinstance(data_directory, str):
            raise ConfigurationFileError(
                "data.directory must be a string."
            )

        if not isinstance(rclone_remote, str):
            raise ConfigurationFileError(
                "storage.rclone_remote must be a string."
            )

        return cls(
            vault_root=(
                Path(vault_root)
                .expanduser()
                .resolve()
            ),
            data_directory=(
                Path(data_directory)
                .expanduser()
                .resolve()
            ),
            rclone_remote=rclone_remote.strip(),
        )

    def save(
        self,
        path: str | Path = DEFAULT_CONFIG_PATH,
    ) -> None:
        config_path = (
            Path(path)
            .expanduser()
            .resolve()
        )

        config_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        content = (
            "[vault]\n"
            f'root = "{_toml_string(self.vault_root)}"\n'
            "\n"
            "[data]\n"
            f'directory = "{_toml_string(self.data_directory)}"\n'
            "\n"
            "[storage]\n"
            f'rclone_remote = "{_toml_string(self.rclone_remote)}"\n'
        )

        try:
            config_path.write_text(
                content,
                encoding="utf-8",
            )

        except OSError as exc:
            raise ConfigurationFileError(
                "Could not write CloudBin configuration: "
                f"{config_path}"
            ) from exc


def _toml_string(value: str | Path) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
    )