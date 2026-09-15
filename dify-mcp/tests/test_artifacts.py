from __future__ import annotations

import pytest

from dify_mcp.artifacts import PreviewNotFound, PreviewStore


def test_store_returns_copy_and_delete() -> None:
    store = PreviewStore(ttl_seconds=60)
    source = {"workflow": {"graph": {"nodes": []}}}
    item = store.put(
        source,
        target_app_id="app",
        base_hash="hash",
        metadata={"kind": "workflow_patch", "operations": [{"op": "test"}]},
    )
    loaded = store.get(item.preview_id)
    loaded.document["workflow"]["graph"]["nodes"].append({"id": "changed"})
    loaded.metadata["operations"].append({"op": "remove"})
    assert store.get(item.preview_id).document == source
    assert store.get(item.preview_id).metadata["operations"] == [{"op": "test"}]
    store.delete(item.preview_id)
    with pytest.raises(PreviewNotFound):
        store.get(item.preview_id)
