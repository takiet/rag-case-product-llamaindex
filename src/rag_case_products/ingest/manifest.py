"""Ingest manifest — read/write storage/<name>/manifest.json (SPEC §5.2 step 1, 7)."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


class Manifest:
    def __init__(self, storage_dir: Path) -> None:
        self._path = storage_dir / "manifest.json"
        self._data: dict[str, dict] = {}
        if self._path.exists():
            try:
                text = self._path.read_text()
                self._data = json.loads(text) if text.strip() else {}
            except json.JSONDecodeError:
                log.warning("Manifest at %s is corrupt; starting from empty.", self._path)
                self._data = {}

    def is_new(self, url: str) -> bool:
        return url not in self._data

    def record(
        self,
        *,
        url: str,
        content_hash: str,
        doc_id: str,
        node_ids: list[str],
        entity_kind: str,
    ) -> None:
        self._data[url] = {
            "url": url,
            "content_hash": content_hash,
            "doc_id": doc_id,
            "node_ids": node_ids,
            "entity_kind": entity_kind,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }

    def remove(self, url: str) -> None:
        self._data.pop(url, None)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2))

    def get(self, url: str) -> dict | None:
        return self._data.get(url)
