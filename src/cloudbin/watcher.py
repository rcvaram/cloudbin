from __future__ import annotations

import logging
import threading
from os import fsdecode
from pathlib import Path

from watchdog.events import (
    FileClosedEvent,
    FileClosedNoWriteEvent,
    FileCreatedEvent,
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from cloudbin.worker import WorkerService

logger = logging.getLogger(__name__)

STUB_SUFFIX = ".sv"

# FileCreatedEvent is used as a fallback for operations such as
# Ctrl+X -> Ctrl+V where Linux may report only a creation event.
STABILITY_CHECK_DELAY_SECONDS = 0.5

# Number of consecutive checks during which size and modification time
# must remain unchanged before the file is considered stable.
STABILITY_REQUIRED_CHECKS = 2


class VaultDropEventHandler(FileSystemEventHandler):
    """
    Converts filesystem events into CloudBin worker operations.

    CloudBin archives files, not directories.

    Directories are observed only so that their child file events can
    be captured recursively by Watchdog.

    File processing is triggered by:
        - FileClosedEvent
        - FileClosedNoWriteEvent
        - FileMovedEvent
        - Stable FileCreatedEvent fallback

    The stability fallback handles filesystem operations such as
    Ctrl+X -> Ctrl+V where Watchdog may report FileCreatedEvent without
    a subsequent FileClosedEvent.
    """

    def __init__(self, worker: WorkerService) -> None:
        super().__init__()

        self.worker = worker

        # Files waiting for the FileCreatedEvent stability fallback.
        self._pending_checks: dict[Path, threading.Timer] = {}

        # Files currently being processed.
        #
        # This prevents a delayed stability check from starting a second
        # worker operation after FileClosedEvent already processed the file.
        self._processing: set[Path] = set()

        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Diagnostic logging
    # ------------------------------------------------------------------

    def on_any_event(
            self,
            event: FileSystemEvent,
    ) -> None:
        """
        Log every filesystem event received by Watchdog.
        """

        source = self._to_path(event.src_path)

        destination = getattr(
            event,
            "dest_path",
            None,
        )

        if destination is not None:
            logger.info(
                "FS EVENT | type=%s | directory=%s | src=%s | dest=%s",
                type(event).__name__,
                event.is_directory,
                source,
                self._to_path(destination),
            )
            return

        logger.info(
            "FS EVENT | type=%s | directory=%s | src=%s",
            type(event).__name__,
            event.is_directory,
            source,
        )

    # ------------------------------------------------------------------
    # Created
    # ------------------------------------------------------------------

    def on_created(
            self,
            event: FileCreatedEvent,
    ) -> None:
        path = self._to_path(event.src_path)

        if self._should_ignore(path):
            logger.debug(
                "Ignoring CloudBin stub: %s",
                path,
            )
            return

        if event.is_directory:
            logger.info(
                "DIRECTORY RECEIVED | %s",
                path,
            )

            # Directories are never processed directly.
            #
            # The observer is recursive, so child files will generate
            # their own filesystem events.
            return

        logger.info(
            "FILE CREATED | %s",
            path,
        )

        # IMPORTANT:
        #
        # Do not process immediately. The file may still be copying.
        #
        # This fallback exists specifically for cases such as
        # Ctrl+X -> Ctrl+V where FileClosedEvent may never arrive.
        self._schedule_stability_check(path)

    # ------------------------------------------------------------------
    # Closed
    # ------------------------------------------------------------------

    def on_closed(
            self,
            event: FileClosedEvent,
    ) -> None:
        if event.is_directory:
            return

        path = self._to_path(event.src_path)

        if self._should_ignore(path):
            logger.debug(
                "Ignoring CloudBin stub: %s",
                path,
            )
            return

        logger.info(
            "FILE CLOSED | %s",
            path,
        )

        # The normal completion event has arrived.
        #
        # There is no reason to keep the FileCreatedEvent fallback timer.
        self._cancel_stability_check(path)

        self._process_file(path)

    # ------------------------------------------------------------------
    # Closed without write
    # ------------------------------------------------------------------

    def on_closed_no_write(
            self,
            event: FileClosedNoWriteEvent,
    ) -> None:
        """
        Handle files that were opened without a write event.

        This remains an intentional fallback mechanism.
        """

        if event.is_directory:
            return

        path = self._to_path(event.src_path)

        if self._should_ignore(path):
            logger.debug(
                "Ignoring CloudBin stub: %s",
                path,
            )
            return

        logger.warning(
            "FILE CLOSED WITHOUT WRITE | %s",
            path,
        )

        self._cancel_stability_check(path)

        self._process_file(path)

    # ------------------------------------------------------------------
    # Moved
    # ------------------------------------------------------------------

    def on_moved(
            self,
            event: FileMovedEvent,
    ) -> None:
        source = self._to_path(event.src_path)
        destination = self._to_path(event.dest_path)

        if self._should_ignore(destination):
            logger.debug(
                "Ignoring CloudBin stub: %s",
                destination,
            )
            return

        if event.is_directory:
            logger.info(
                "DIRECTORY RECEIVED | %s -> %s",
                source,
                destination,
            )

            # Do not process directories.
            return

        logger.info(
            "FILE MOVED | %s -> %s",
            source,
            destination,
        )

        # A moved file may not receive FileClosedEvent.
        #
        # Use the same stability protection as FileCreatedEvent.
        self._schedule_stability_check(destination)

    # ------------------------------------------------------------------
    # Stability fallback
    # ------------------------------------------------------------------

    def _schedule_stability_check(
            self,
            path: Path,
    ) -> None:
        """
        Schedule a delayed stability check for a file.

        If the same file receives another creation/move event before the
        timer runs, the existing timer is replaced.

        The timer never blocks Watchdog's event-processing thread.
        """

        path = path.resolve()

        with self._lock:
            if path in self._processing:
                logger.debug(
                    "File is already being processed: %s",
                    path,
                )
                return

            existing_timer = self._pending_checks.pop(
                path,
                None,
            )

            if existing_timer is not None:
                existing_timer.cancel()

            timer = threading.Timer(
                STABILITY_CHECK_DELAY_SECONDS,
                self._check_file_stability,
                args=(path,),
            )

            # Do not prevent application shutdown.
            timer.daemon = True

            self._pending_checks[path] = timer

            timer.start()

        logger.debug(
            "Stability check scheduled | %s",
            path,
        )

    def _check_file_stability(
            self,
            path: Path,
            previous_size: int | None = None,
            previous_mtime_ns: int | None = None,
            stable_checks: int = 0,
    ) -> None:
        """
        Check whether a file has stopped changing.

        The file must remain unchanged for the required number of
        consecutive checks before processing begins.
        """

        with self._lock:
            if path in self._processing:
                self._pending_checks.pop(
                    path,
                    None,
                )
                return

        if not path.exists():
            logger.warning(
                "STABILITY CHECK | file no longer exists | %s",
                path,
            )

            with self._lock:
                self._pending_checks.pop(
                    path,
                    None,
                )

            return

        if not path.is_file():
            logger.warning(
                "STABILITY CHECK | path is not a file | %s",
                path,
            )

            with self._lock:
                self._pending_checks.pop(
                    path,
                    None,
                )

            return

        try:
            stat = path.stat()

        except OSError as exc:
            logger.warning(
                "STABILITY CHECK | could not stat file | %s | %s",
                path,
                exc,
            )

            self._reschedule_stability_check(
                path,
                previous_size=previous_size,
                previous_mtime_ns=previous_mtime_ns,
                stable_checks=stable_checks,
            )

            return

        current_size = stat.st_size
        current_mtime_ns = stat.st_mtime_ns

        unchanged = (
                previous_size is not None
                and previous_mtime_ns is not None
                and current_size == previous_size
                and current_mtime_ns == previous_mtime_ns
        )

        if unchanged:
            stable_checks += 1
        else:
            stable_checks = 0

        logger.debug(
            "STABILITY CHECK | %s | size=%d | mtime_ns=%d | "
            "stable_checks=%d/%d",
            path,
            current_size,
            current_mtime_ns,
            stable_checks,
            STABILITY_REQUIRED_CHECKS,
        )

        if stable_checks >= STABILITY_REQUIRED_CHECKS:
            with self._lock:
                self._pending_checks.pop(
                    path,
                    None,
                )

            logger.info(
                "FILE STABLE | %s",
                path,
            )

            self._process_file(path)
            return

        self._reschedule_stability_check(
            path,
            previous_size=current_size,
            previous_mtime_ns=current_mtime_ns,
            stable_checks=stable_checks,
        )

    def _reschedule_stability_check(
            self,
            path: Path,
            *,
            previous_size: int | None,
            previous_mtime_ns: int | None,
            stable_checks: int,
    ) -> None:
        """
        Schedule the next stability check without creating duplicate
        timers.
        """

        with self._lock:
            if path in self._processing:
                self._pending_checks.pop(
                    path,
                    None,
                )
                return

            timer = threading.Timer(
                STABILITY_CHECK_DELAY_SECONDS,
                self._check_file_stability,
                kwargs={
                    "path": path,
                    "previous_size": previous_size,
                    "previous_mtime_ns": previous_mtime_ns,
                    "stable_checks": stable_checks,
                },
            )

            timer.daemon = True

            self._pending_checks[path] = timer

            timer.start()

    def _cancel_stability_check(
            self,
            path: Path,
    ) -> None:
        path = path.resolve()

        with self._lock:
            timer = self._pending_checks.pop(
                path,
                None,
            )

        if timer is not None:
            timer.cancel()

            logger.debug(
                "Stability check cancelled | %s",
                path,
            )

    # ------------------------------------------------------------------
    # File processing
    # ------------------------------------------------------------------

    def _process_file(
            self,
            path: Path,
    ) -> None:
        path = path.resolve()

        with self._lock:
            if path in self._processing:
                logger.debug(
                    "Duplicate processing request ignored | %s",
                    path,
                )
                return

            self._processing.add(path)

        try:
            if not path.exists():
                logger.warning(
                    "FILE NO LONGER EXISTS | %s",
                    path,
                )
                return

            if not path.is_file():
                logger.warning(
                    "EXPECTED FILE BUT PATH IS NOT A FILE | %s",
                    path,
                )
                return

            logger.info(
                "PROCESSING FILE | %s",
                path,
            )

            self.worker.process_file(path)

            logger.info(
                "FILE PROCESSING COMPLETE | %s",
                path,
            )

        except Exception:
            logger.exception(
                "FILE PROCESSING FAILED | %s",
                path,
            )

        finally:
            with self._lock:
                self._processing.discard(path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _should_ignore(
            path: Path,
    ) -> bool:
        return path.name.endswith(STUB_SUFFIX)

    @staticmethod
    def _to_path(
            value: str | bytes,
    ) -> Path:
        return Path(fsdecode(value))


class VaultDropWatcher:
    """
    Watches VaultDrop recursively.

    Directories are never processed directly.

    Individual files are processed through their filesystem lifecycle:

        FileClosedEvent
            OR
        FileClosedNoWriteEvent
            OR
        FileMovedEvent stability fallback
            OR
        FileCreatedEvent stability fallback
    """

    def __init__(
            self,
            watch_path: str | Path,
            worker: WorkerService,
    ) -> None:
        self.watch_path = (
            Path(watch_path)
            .expanduser()
            .resolve()
        )

        self.worker = worker
        self._observer: BaseObserver | None = None

        self._validate_configuration()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_configuration(self) -> None:
        if not self.watch_path.exists():
            raise FileNotFoundError(
                f"Watch path does not exist: "
                f"{self.watch_path}"
            )

        if not self.watch_path.is_dir():
            raise ValueError(
                f"Watch path is not a directory: "
                f"{self.watch_path}"
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._observer is not None:
            raise RuntimeError(
                "VaultDropWatcher is already started."
            )

        handler = VaultDropEventHandler(
            self.worker
        )

        observer = Observer()

        observer.schedule(
            handler,
            str(self.watch_path),
            recursive=True,
        )

        observer.start()

        self._observer = observer

        logger.info(
            "VaultDrop watcher started: %s",
            self.watch_path,
        )

    def stop(self) -> None:
        observer = self._observer

        if observer is None:
            return

        logger.info(
            "Stopping VaultDrop watcher."
        )

        observer.stop()
        observer.join()

        self._observer = None

        logger.info(
            "VaultDrop watcher stopped."
        )

    def run(self) -> None:
        self.start()

        observer = self._observer

        if observer is None:
            raise RuntimeError(
                "Watcher failed to start."
            )

        try:
            observer.join()

        except KeyboardInterrupt:
            logger.info(
                "Keyboard interrupt received."
            )

        finally:
            self.stop()
