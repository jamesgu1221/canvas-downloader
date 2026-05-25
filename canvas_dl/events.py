from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class SyncEvent:
    pass


@dataclass(frozen=True)
class LogEvent(SyncEvent):
    message: str


@dataclass(frozen=True)
class RunStarted(SyncEvent):
    canvas_url: str
    download_dir: Path
    dry_run: bool


@dataclass(frozen=True)
class CoursesSynced(SyncEvent):
    enabled_count: int
    disabled_count: int
    added: list[str]
    inactive: list[str]


@dataclass(frozen=True)
class CourseProgressStarted(SyncEvent):
    total: int


@dataclass(frozen=True)
class CourseProgressTick(SyncEvent):
    step: int = 1


@dataclass(frozen=True)
class FileProgressStarted(SyncEvent):
    course_name: str
    total: int


@dataclass(frozen=True)
class FileProgressTick(SyncEvent):
    step: int = 1


@dataclass(frozen=True)
class FilePostfix(SyncEvent):
    text: str


class FileProgressEnded(SyncEvent):
    pass


@dataclass(frozen=True)
class VideoBytesStarted(SyncEvent):
    asset_key: str
    label: str
    total_bytes: int


@dataclass(frozen=True)
class VideoBytesProgress(SyncEvent):
    asset_key: str
    downloaded: int
    total: int


@dataclass(frozen=True)
class VideoBytesFinished(SyncEvent):
    asset_key: str
    success: bool


@dataclass(frozen=True)
class CourseFinished(SyncEvent):
    course_name: str
    downloaded: int
    skipped: int
    failed: int


@dataclass(frozen=True)
class RunFinished(SyncEvent):
    downloaded: int
    cancelled: bool = False


class Reporter(Protocol):
    def emit(self, event: SyncEvent) -> None:
        ...


class NullReporter:
    def emit(self, event: SyncEvent) -> None:
        pass


class CallbackReporter:
    def __init__(self, callback):
        self._callback = callback

    def emit(self, event: SyncEvent) -> None:
        self._callback(event)
