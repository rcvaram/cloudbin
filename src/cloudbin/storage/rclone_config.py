from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass


class RcloneConfigError(RuntimeError):
    """Base exception for rclone configuration failures."""


class RcloneNotFoundError(RcloneConfigError):
    """Raised when rclone cannot be found."""


class RcloneConfigCommandError(RcloneConfigError):
    """Raised when a rclone configuration command fails."""


@dataclass(frozen=True)
class RcloneRemote:
    name: str
    remote_type: str

    @property
    def is_crypt(self) -> bool:
        return self.remote_type == "crypt"


class RcloneConfig:

    def __init__(self, executable: str = "rclone", ) -> None:
        self.executable = executable
        self._validate_configuration()

    def _validate_configuration(self) -> None:
        if shutil.which(self.executable) is None:
            raise RcloneNotFoundError(f"Could not find rclone executable: {self.executable}")

    def _run(self, *arguments: str, ) -> subprocess.CompletedProcess[str]:
        command = [self.executable, *arguments]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except OSError as exc:
            raise RcloneConfigCommandError(f"Failed to execute rclone: {exc}") from exc

        if result.returncode != 0:
            stderr = result.stderr.strip()

            if not stderr:
                stderr = "rclone returned no error message."

            raise RcloneConfigCommandError(f"rclone configuration command failed {result.returncode}): {stderr}")

        return result

    def list_remotes(self) -> list[RcloneRemote]:
        result = self._run("config", "dump")
        try:
            configuration = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RcloneConfigCommandError("rclone returned invalid JSON  while reading its configuration.") from exc

        if not isinstance(configuration, dict):
            raise RcloneConfigCommandError("Unexpected rclone configuration format.")

        remotes: list[RcloneRemote] = []

        for name, settings in configuration.items():
            if not isinstance(name, str):
                continue

            if not isinstance(settings, dict):
                continue

            remote_type = settings.get("type")

            if not isinstance(remote_type, str):
                continue

            remotes.append(RcloneRemote(name=name, remote_type=remote_type))

        remotes.sort(key=lambda remote: remote.name.lower())

        return remotes

    def list_crypt_remotes(self) -> list[RcloneRemote]:
        return [remote for remote in self.list_remotes() if remote.is_crypt]

    def get_remote(self, name: str, ) -> RcloneRemote:
        if not isinstance(name, str):
            raise TypeError("Remote name must be a string.")

        normalized_name = name.strip().removesuffix(":")

        if not normalized_name:
            raise ValueError("Remote name cannot be empty.")

        for remote in self.list_remotes():
            if remote.name == normalized_name:
                return remote

        raise RcloneConfigError(f"rclone remote does not exist:{normalized_name}")

    def require_crypt_remote(self, name: str) -> RcloneRemote:
        remote = self.get_remote(name)

        if not remote.is_crypt:
            raise RcloneConfigError(
                f"Remote '{remote.name}' is not an rclone crypt remote. Detected type: '{remote.remote_type}'.")

        return remote
