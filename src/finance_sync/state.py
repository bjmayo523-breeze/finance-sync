"""Small local state (last successful sync) and a run lock."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from finance_sync import paths


class State:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.STATE_FILE
        self.data: dict = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError:
                self.data = {}

    @property
    def last_success(self) -> datetime | None:
        v = self.data.get("last_success_utc")
        return datetime.fromisoformat(v) if v else None

    def mark_success(self, when: datetime) -> None:
        self.data["last_success_utc"] = when.astimezone(timezone.utc).isoformat()
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


class RunLock:
    """Prevents two syncs at once (e.g. daily trigger + logon trigger)."""

    STALE_SECONDS = 3600

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.LOCK_FILE
        self._fd: int | None = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and time.time() - self.path.stat().st_mtime > self.STALE_SECONDS:
            self.path.unlink(missing_ok=True)
        try:
            self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError("Another sync is already running (data/sync.lock).") from None
        os.write(self._fd, str(os.getpid()).encode())
        return self

    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            os.close(self._fd)
        self.path.unlink(missing_ok=True)
