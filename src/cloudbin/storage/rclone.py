from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

DEFAULT_REMOTE = None #gdrive-crypt:
DEFAULT_TIMEOUT_SECONDS = 300


class RcloneError(RuntimeError):
    """Base exception for SecureVault rclone failures."""


class RcloneNotFoundError(RcloneError):
    """Raised when rclone cannot be found."""


class RcloneCommandError(RcloneError):
    """Raised when an rclone command fails."""


class RcloneClient:

    def __init__(self, remote: str, executable: str = "rclone",
                 timeout: int = DEFAULT_TIMEOUT_SECONDS, ) -> None:
        self.remote = remote
        self.executable = executable
        self.timeout = timeout
        self._validate_configuration()

    def _validate_configuration(self) -> None:
        if not self.remote:
            raise ValueError("rclone remote cannot be empty.")

        if not self.remote.endswith(":"):
            raise ValueError("rclone remote must end with ':'. "  "Example: 'gdrive-crypt:'")


        if shutil.which(self.executable) is None:
            raise RcloneNotFoundError(f"Could not find rclone executable: {self.executable}")

        if self.timeout <= 0:
            raise ValueError("rclone timeout must be greater than zero.")

    def _run(self, *arguments: str, timeout: int | None = None, ) -> subprocess.CompletedProcess[str]:

        command = [self.executable, *arguments, ]

        effective_timeout = (timeout if timeout is not None else self.timeout)

        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=effective_timeout, check=False, )

        except subprocess.TimeoutExpired as exc:
            raise RcloneCommandError(f"rclone command timed out after " f"{effective_timeout} seconds.") from exc

        except OSError as exc:
            raise RcloneCommandError(f"Failed to execute rclone: {exc}") from exc

        if result.returncode != 0:
            stderr = result.stderr.strip()

            if not stderr:
                stderr = "rclone returned no error message."

            raise RcloneCommandError(f"rclone command failed " f"(exit code {result.returncode}): {stderr}")

        return result

    def _build_remote_path(self, cloud_relpath: str, ) -> str:

        if not isinstance(cloud_relpath, str):
            raise TypeError("cloud_relpath must be a string.")

        normalized = cloud_relpath.strip()

        if not normalized:
            raise ValueError("cloud_relpath cannot be empty.")

        # Always use POSIX-style paths for rclone remotes.
        normalized = normalized.replace("\\", "/")

        # Prevent absolute remote paths.
        normalized = normalized.lstrip("/")

        parts = normalized.split("/")

        # Prevent path traversal.
        if any(part == ".." for part in parts):
            raise ValueError("cloud_relpath must not contain '..'.")

        # Ignore redundant "." components.
        parts = [part for part in parts if part not in ("", ".")]

        if not parts:
            raise ValueError("cloud_relpath does not contain a valid path.")

        normalized = "/".join(parts)

        return f"{self.remote}{normalized}"

    def upload_to_drive(self, local_path: str | Path, cloud_relpath: str, ) -> None:

        source = Path(local_path).expanduser()

        if not source.exists():
            raise FileNotFoundError(f"Local file does not exist: {source}")

        if not source.is_file():
            raise ValueError(f"Local path is not a regular file: {source}")

        source = source.resolve()

        remote_path = self._build_remote_path(cloud_relpath)

        self._run("copyto", str(source), remote_path, )

    # ------------------------------------------------------------------
    # Download / Restore
    # ------------------------------------------------------------------

    def download_from_drive(self, cloud_relpath: str, local_path: str | Path, ) -> None:

        remote_path = self._build_remote_path(cloud_relpath)

        destination = (Path(local_path).expanduser().resolve())

        destination.parent.mkdir(parents=True, exist_ok=True)

        self._run("copyto", remote_path, str(destination))

    def verify_cloud_file(self, cloud_relpath: str, expected_size: int | None = None) -> dict[str, Any]:
        remote_path = self._build_remote_path(cloud_relpath)

        result = self._run("lsjson", remote_path, "--files-only", )

        try:
            entries = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RcloneCommandError("rclone returned invalid JSON while " "verifying the remote file.") from exc

        if not isinstance(entries, list):
            raise RcloneCommandError("Unexpected rclone lsjson response.")

        if len(entries) == 0:
            raise RcloneCommandError(f"Remote file does not exist: {cloud_relpath}")

        if len(entries) != 1:
            raise RcloneCommandError(
                f"Expected exactly one remote file for " f"{cloud_relpath}, but found {len(entries)}.")

        metadata = entries[0]

        if not isinstance(metadata, dict):
            raise RcloneCommandError("Unexpected metadata returned by rclone.")

        if expected_size is not None:
            remote_size = metadata.get("Size")

            if remote_size != expected_size:
                raise RcloneCommandError(
                    "Remote file size mismatch: " f"expected {expected_size}, " f"got {remote_size}.")

        return metadata

    def cloud_file_exists(self, cloud_relpath: str) -> bool:
        """
        Return True if the file exists on the encrypted remote.

        This is useful for idempotency checks before uploading.
        """

        remote_path = self._build_remote_path(cloud_relpath)

        result = self._run("lsjson", remote_path, "--files-only")

        try:
            entries = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RcloneCommandError("rclone returned invalid JSON.") from exc

        return bool(entries)

    # ------------------------------------------------------------------
    # Strong verification
    # ------------------------------------------------------------------

    def verify_downloaded_file(self, cloud_relpath: str, expected_sha256: str, temporary_path: str | Path) -> None:

        import hashlib

        temporary = (Path(temporary_path).expanduser().resolve())

        try:
            self.download_from_drive(cloud_relpath, temporary)

            digest = hashlib.sha256()

            with temporary.open("rb") as file:
                while chunk := file.read(64 * 1024):
                    digest.update(chunk)

            actual_sha256 = digest.hexdigest()

            if actual_sha256 != expected_sha256:
                raise RcloneCommandError("SHA-256 verification failed for " f"{cloud_relpath}: "
                                         f"expected {expected_sha256}, "
                                         f"got {actual_sha256}.")
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
