from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DATABASE_FILENAME = "cloudbin.sqlite"
CONFIG_FILENAME = "config.toml"
ACTIVE_CONFIG_FILENAME = "active.toml"

DEFAULT_CLOUDBIN_ROOT = Path("~/CloudBin").expanduser()
GLOBAL_CONFIG_DIRECTORY = Path("~/.config/cloudbin").expanduser()
ACTIVE_CONFIG_PATH = (GLOBAL_CONFIG_DIRECTORY / ACTIVE_CONFIG_FILENAME)


class ConfigurationError(RuntimeError):
    """Base exception for CloudBin configuration errors."""


class ConfigurationFileError(ConfigurationError):
    """Raised when a configuration file cannot be read or parsed."""


@dataclass(frozen=True)
class CloudBinConfig:
    """
    CloudBin runtime configuration.

    The user configures only the CloudBin root directory and
    the rclone crypt remote.

    All other paths are derived from the root directory.
    """

    root_directory: Path
    rclone_remote: str

    def __post_init__(self) -> None:
        root = (Path(self.root_directory).expanduser().resolve())

        object.__setattr__(self, "root_directory", root)

        remote = self.rclone_remote.strip()

        if not remote:
            raise ValueError("rclone_remote cannot be empty.")

        if not remote.endswith(":"):
            raise ValueError("rclone_remote must end with ':'. "  "Example: 'my-cloudbin:'")

        object.__setattr__(self, "rclone_remote", remote)

    # ------------------------------------------------------------------
    # CloudBin directories
    # ------------------------------------------------------------------

    @property
    def internal_directory(self) -> Path:
        """Internal CloudBin state directory."""
        return self.root_directory / ".cloudbin"

    @property
    def config_path(self) -> Path:
        """CloudBin configuration file."""
        return self.internal_directory / CONFIG_FILENAME

    @property
    def database_path(self) -> Path:
        """CloudBin SQLite database."""
        return self.internal_directory / DATABASE_FILENAME

    @property
    def vault_root(self) -> Path:
        """Directory watched by CloudBin."""
        return self.root_directory / "VaultDrop"

    @property
    def restore_root(self) -> Path:
        """Default restore destination."""
        return self.root_directory / "Restored"

    @property
    def temporary_root(self) -> Path:
        """Temporary CloudBin working directory."""
        return self.internal_directory / "tmp"

    # ------------------------------------------------------------------
    # Directory initialization
    # ------------------------------------------------------------------

    def initialize_directories(self) -> None:
        """
        Create the CloudBin directory structure.

        Creates:

            <root>/
                .cloudbin/
                    tmp/
                VaultDrop/
                Restored/
        """
        self.internal_directory.mkdir(parents=True, exist_ok=True)

        self.vault_root.mkdir(parents=True, exist_ok=True)

        self.restore_root.mkdir(parents=True, exist_ok=True)

        self.temporary_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # CloudBin configuration loading
    # ------------------------------------------------------------------

    @classmethod
    def load(
            cls,
            path: str | Path,
    ) -> CloudBinConfig:
        """
        Load CloudBin configuration from a config.toml file.
        """
        config_path = (Path(path).expanduser().resolve())

        if not config_path.exists():
            raise ConfigurationFileError(f"CloudBin configuration file does not exist  {config_path}")

        if not config_path.is_file():
            raise ConfigurationFileError(f"CloudBin configuration path is not a file {config_path}")

        try:
            with config_path.open("rb") as file:
                data = tomllib.load(file)

        except OSError as exc:
            raise ConfigurationFileError(f"Could not read CloudBin configuration: {config_path}") from exc

        except tomllib.TOMLDecodeError as exc:
            raise ConfigurationFileError(f"Invalid TOML configuration: {config_path}") from exc

        return cls._from_data(data)

    @classmethod
    def from_root(cls, root_directory: str | Path) -> CloudBinConfig:
        """
        Load a CloudBin configuration from a CloudBin root directory.
        """
        root = (Path(root_directory).expanduser().resolve())

        config_path = root / ".cloudbin" / CONFIG_FILENAME

        return cls.load(config_path)

    @classmethod
    def load_active(cls) -> CloudBinConfig:
        """
        Load the currently active CloudBin configuration.

        The active CloudBin is identified by:

            ~/.config/cloudbin/active.toml
        """
        root = load_active_root()

        return cls.from_root(root)

    @classmethod
    def _from_data(cls, data: dict) -> CloudBinConfig:
        try:
            cloudbin = data["cloudbin"]
            storage = data["storage"]

            root_directory = cloudbin["root"]
            rclone_remote = storage["rclone_remote"]

        except (KeyError, TypeError) as exc:
            raise ConfigurationFileError("CloudBin configuration is missing required fields.") from exc

        if not isinstance(root_directory, str):
            raise ConfigurationFileError("cloudbin.root must be a string.")

        if not isinstance(rclone_remote, str):
            raise ConfigurationFileError("storage.rclone_remote must be a string.")

        try:
            return cls(root_directory=(Path(root_directory).expanduser().resolve()), rclone_remote=rclone_remote)

        except ValueError as exc:
            raise ConfigurationFileError(str(exc)) from exc

    # ------------------------------------------------------------------
    # CloudBin configuration saving
    # ------------------------------------------------------------------

    def save(self, path: str | Path | None = None) -> None:
        """
        Save CloudBin configuration.

        If no path is provided, configuration is saved inside:

            <root>/.cloudbin/config.toml
        """
        config_path = self.config_path if path is None else (Path(path).expanduser().resolve())

        config_path.parent.mkdir(parents=True, exist_ok=True)

        content = (
            "[cloudbin]\n"
            f'root = "{_toml_string(self.root_directory)}"\n'
            "\n"
            "[storage]\n"
            f'rclone_remote = "{_toml_string(self.rclone_remote)}"\n'
        )

        try:
            config_path.write_text(content, encoding="utf-8")

        except OSError as exc:
            raise ConfigurationFileError(f"Could not write CloudBin configuration: {config_path}") from exc

    # ------------------------------------------------------------------
    # Active CloudBin
    # ------------------------------------------------------------------

    def set_active(self) -> None:
        """
        Make this CloudBin the globally active CloudBin.

        The global file contains only the root directory.
        """
        save_active_root(self.root_directory)


# ----------------------------------------------------------------------
# Active CloudBin pointer
# ----------------------------------------------------------------------


def save_active_root(root_directory: str | Path, path: str | Path = ACTIVE_CONFIG_PATH) -> None:
    """
    Save the root directory of the active CloudBin.
    """
    root = Path(root_directory).expanduser().resolve()
    active_path = Path(path).expanduser().resolve()

    active_path.parent.mkdir(parents=True, exist_ok=True)

    content = ("[cloudbin]\n"
               f'root = "{_toml_string(root)}"\n')

    try:
        active_path.write_text(content, encoding="utf-8")

    except OSError as exc:
        raise ConfigurationFileError(f"Could not save active CloudBin configuration: {active_path}") from exc


def load_active_root(path: str | Path = ACTIVE_CONFIG_PATH) -> Path:
    """
    Load the root directory of the active CloudBin.
    """
    active_path = Path(path).expanduser().resolve()

    if not active_path.exists():
        raise ConfigurationFileError("No active CloudBin has been configured.")

    if not active_path.is_file():
        raise ConfigurationFileError(f"Active CloudBin configuration path is not a file: {active_path}")

    try:
        with active_path.open("rb") as file:
            data = tomllib.load(file)

    except OSError as exc:
        raise ConfigurationFileError(f"Could not read active CloudBin configuration: {active_path}") from exc

    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationFileError(f"Invalid active CloudBin configuration: {active_path}") from exc

    try:
        cloudbin = data["cloudbin"]
        root = cloudbin["root"]

    except (KeyError, TypeError) as exc:
        raise ConfigurationFileError("Active CloudBin configuration is missing cloudbin.root.") from exc

    if not isinstance(root, str):
        raise ConfigurationFileError("Active CloudBin cloudbin.root must be a string.")

    return (Path(root).expanduser().resolve())


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _toml_string(value: str | Path) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')
