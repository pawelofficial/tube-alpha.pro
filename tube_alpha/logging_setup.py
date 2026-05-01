"""Log routing by originating Python file (pathname under project root)."""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path


_SAFE_KEY = re.compile(r"[^A-Za-z0-9._-]+")


class PerModuleFileHandler(logging.Handler):
    """Write records to ``log_dir / <path_based>.log``.

    Emit path → log stem uses ``record.pathname`` resolved relative to ``project_root``
    (e.g. ``tube_alpha/services/sentiment.py`` → ``tube_alpha_services_sentiment.log``).
    Anything outside ``project_root`` (stdlib, site-packages, uvicorn, httpx, …) → ``external.log``.
    """

    def __init__(self, log_dir: Path, project_root: Path, encoding: str = "utf-8"):
        super().__init__()
        self.log_dir = Path(log_dir)
        self.project_root = Path(project_root).resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.encoding = encoding
        self._handlers: dict[str, logging.FileHandler] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _sanitize_key(key: str) -> str:
        key = _SAFE_KEY.sub("_", key).strip("._-")
        return key or "unknown"

    def _record_log_key(self, record: logging.LogRecord) -> str:
        pathname = getattr(record, "pathname", None) or ""
        if not pathname:
            name = getattr(record, "name", "") or "root"
            leaf = name.split(".")[-1] if name else "unknown"
            return PerModuleFileHandler._sanitize_key(leaf)

        try:
            path = Path(pathname).resolve()
            root = self.project_root
            try:
                rel = path.relative_to(root)
            except ValueError:
                return "external"

            parts = list(rel.parts)
            if parts:
                last = Path(parts[-1])
                if last.suffix == ".py":
                    parts[-1] = last.stem
            key = "_".join(parts) if parts else "unknown"
            return self._sanitize_key(key)
        except OSError:
            return "external"

    def setFormatter(self, fmt: logging.Formatter | None) -> None:
        super().setFormatter(fmt)
        with self._lock:
            for h in self._handlers.values():
                if fmt is not None:
                    h.setFormatter(fmt)

    def emit(self, record: logging.LogRecord) -> None:
        stem = self._record_log_key(record)
        with self._lock:
            if stem not in self._handlers:
                path = self.log_dir / f"{stem}.log"
                fh = logging.FileHandler(path, encoding=self.encoding)
                fh.setLevel(logging.NOTSET)
                fmt = self.formatter
                if fmt is not None:
                    fh.setFormatter(fmt)
                self._handlers[stem] = fh
            handler = self._handlers[stem]
        handler.emit(record)

    def close(self) -> None:
        with self._lock:
            for h in self._handlers.values():
                h.close()
            self._handlers.clear()
        super().close()
