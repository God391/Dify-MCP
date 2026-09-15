"""Short-lived, in-memory previews used to separate generation from writes."""

from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class PreviewArtifact:
    preview_id: str
    document: dict[str, Any]
    target_app_id: str | None
    base_hash: str | None
    metadata: dict[str, Any]
    created_at: float


class PreviewNotFound(ValueError):
    pass


class PreviewStore:
    def __init__(self, *, ttl_seconds: int = 1800, max_items: int = 50):
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._items: dict[str, PreviewArtifact] = {}
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        expired = [key for key, item in self._items.items() if now - item.created_at > self.ttl_seconds]
        for key in expired:
            del self._items[key]
        if len(self._items) >= self.max_items:
            oldest = sorted(self._items.values(), key=lambda item: item.created_at)
            for item in oldest[: len(self._items) - self.max_items + 1]:
                self._items.pop(item.preview_id, None)

    def put(
        self,
        document: dict[str, Any],
        *,
        target_app_id: str | None = None,
        base_hash: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PreviewArtifact:
        now = time.time()
        preview_id = uuid4().hex
        artifact = PreviewArtifact(
            preview_id,
            copy.deepcopy(document),
            target_app_id,
            base_hash,
            copy.deepcopy(metadata or {}),
            now,
        )
        with self._lock:
            self._purge(now)
            self._items[preview_id] = artifact
        return artifact

    def get(self, preview_id: str) -> PreviewArtifact:
        now = time.time()
        with self._lock:
            self._purge(now)
            artifact = self._items.get(preview_id)
            if artifact is None:
                raise PreviewNotFound("Preview not found or expired; generate it again")
            return PreviewArtifact(
                artifact.preview_id,
                copy.deepcopy(artifact.document),
                artifact.target_app_id,
                artifact.base_hash,
                copy.deepcopy(artifact.metadata),
                artifact.created_at,
            )

    def delete(self, preview_id: str) -> None:
        with self._lock:
            self._items.pop(preview_id, None)
