from __future__ import annotations

import logging
from os import fsdecode
from pathlib import Path

from watchdog.events import (FileClosedEvent, FileCreatedEvent, FileMovedEvent, FileSystemEventHandler, FileSystemEvent)
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from vault.worker import VaultWorker

logger = logging.getLogger(__name__)

STUB_SUFFIX = ".sv"


class VaultDropEventHandler(FileSystemEventHandler):

    def __init__(self, worker: VaultWorker) -> None:
        super().__init__()
        self.worker = worker

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        path = self._to_path(event.src_path)

        if self._should_ignore(path):
            return

        logger.debug("File created: %s", path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        path = self._to_path(event.dest_path)

        if self._should_ignore(path):
            return

        logger.info("File moved into VaultDrop: %s", path)

        self._process_file(path)

    def on_closed(self, event: FileClosedEvent) -> None:
        if event.is_directory:
            return

        path = self._to_path(event.src_path)

        if self._should_ignore(path):
            return

        logger.info("File write completed: %s", path)

        self._process_file(path)

    def _process_file(self, path: Path) -> None:
        try:
            self.worker.process_file(path)
        except Exception:
            # The watcher must continue running if Worker processing fails.
            logger.exception("Worker failed while processing: %s", path)

    @staticmethod
    def _should_ignore(path: Path) -> bool:
        return path.name.endswith(STUB_SUFFIX)

    @staticmethod
    def _to_path(value: str | bytes) -> Path:
        return Path(fsdecode(value))


class VaultDropWatcher:
    def __init__(self, watch_path: str | Path, worker: VaultWorker) -> None:
        self.watch_path = Path(watch_path).expanduser().resolve()
        self.worker = worker
        self._observer: BaseObserver | None = None
        self._validate_configuration()

    def _validate_configuration(self) -> None:
        if not self.watch_path.exists():
            raise FileNotFoundError(f"Watch path does not exist: {self.watch_path}")

        if not self.watch_path.is_dir():
            raise ValueError(f"Watch path is not a directory: {self.watch_path}")

    def start(self) -> None:
        if self._observer is not None:
            raise RuntimeError("VaultDropWatcher is already started.")

        handler = VaultDropEventHandler(self.worker)

        observer = Observer()

        observer.schedule(handler, str(self.watch_path), recursive=True,
                          event_filter=[FileCreatedEvent, FileMovedEvent, FileClosedEvent])

        observer.start()

        self._observer = observer

        logger.info("VaultDrop watcher started: %s", self.watch_path)

    def stop(self) -> None:
        observer = self._observer
        if observer is None:
            return
        logger.info("Stopping VaultDrop watcher.")
        observer.stop()
        observer.join()
        self._observer = None
        logger.info("VaultDrop watcher stopped.")

    def run(self) -> None:
        self.start()
        observer = self._observer
        if observer is None:
            raise RuntimeError("Watcher failed to start.")
        try:
            observer.join()
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received.")
        finally:
            self.stop()
