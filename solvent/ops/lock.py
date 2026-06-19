"""Process-level single-writer lock for local state files."""

import json
import os
import time
from pathlib import Path


class SingleWriterLock:
    def __init__(self, path: Path, *, stale_after_s: int = 1800) -> None:
        self.path = path
        self.stale_after_s = stale_after_s
        self._fd: int | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self._fd = os.open(
                    self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
                payload = {
                    "pid": os.getpid(),
                    "created_at": time.time(),
                }
                os.write(self._fd, json.dumps(payload, separators=(",", ":")).encode())
                return self
            except FileExistsError as exc:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age > self.stale_after_s:
                    self.path.unlink(missing_ok=True)
                    continue
                raise RuntimeError(f"state writer already active: {self.path}") from exc

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self.path.unlink(missing_ok=True)
