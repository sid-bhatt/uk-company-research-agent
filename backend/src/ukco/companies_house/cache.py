"""A small on-disk response cache.

Keeps re-runs of the pipeline cheap and kind to the rate limit: company data is
cached for a TTL, filing documents (immutable once filed) are cached forever.
Writes are atomic so parallel pipeline tasks can share one cache directory.
"""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path


class DiskCache:
    def __init__(self, root: Path, *, clock=time.time) -> None:
        self.root = root
        self._clock = clock

    @staticmethod
    def key(*parts: object) -> str:
        return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()

    def _path(self, key: str, suffix: str) -> Path:
        return self.root / key[:2] / f"{key}{suffix}"

    def _fresh(self, path: Path, ttl: float | None) -> bool:
        if not path.exists():
            return False
        return ttl is None or self._clock() - path.stat().st_mtime < ttl

    def get_json(self, key: str, ttl: float | None) -> object | None:
        path = self._path(key, ".json")
        if not self._fresh(path, ttl):
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None  # corrupt or half-written: treat as a miss

    def set_json(self, key: str, value: object) -> None:
        self._write(self._path(key, ".json"), json.dumps(value).encode())

    def get_bytes(self, key: str, ttl: float | None = None) -> bytes | None:
        path = self._path(key, ".bin")
        if not self._fresh(path, ttl):
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None

    def set_bytes(self, key: str, value: bytes) -> None:
        self._write(self._path(key, ".bin"), value)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
