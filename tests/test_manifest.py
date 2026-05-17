"""Tests for ingest/manifest.py — skip decision logic (SPEC §5.2 step 1)."""

import json

import pytest

from rag_case_products.ingest.manifest import Manifest

_URL_A = "https://www.axis.com/products/axis-q3558-lve"
_URL_B = "https://www.axis.com/customer-story/coned-drone-ptz"


@pytest.fixture()
def empty_manifest(tmp_path):
    """Manifest backed by an empty temp directory (no manifest.json yet)."""
    return Manifest(tmp_path)


@pytest.fixture()
def seeded_manifest(tmp_path):
    """Manifest with _URL_A already recorded."""
    m = Manifest(tmp_path)
    m.record(
        url=_URL_A,
        content_hash="abc123",
        doc_id="docid-001",
        node_ids=["node-1", "node-2"],
        entity_kind="product",
    )
    m.save()
    return Manifest(tmp_path)


def test_new_url_on_empty_manifest(empty_manifest):
    assert empty_manifest.is_new(_URL_A) is True


def test_new_url_on_seeded_manifest(seeded_manifest):
    assert seeded_manifest.is_new(_URL_B) is True


def test_ingested_url_is_not_new(seeded_manifest):
    assert seeded_manifest.is_new(_URL_A) is False


def test_record_and_reload(tmp_path):
    m = Manifest(tmp_path)
    m.record(
        url=_URL_A,
        content_hash="deadbeef",
        doc_id="doc-x",
        node_ids=["n1"],
        entity_kind="product",
    )
    m.save()

    reloaded = Manifest(tmp_path)
    assert not reloaded.is_new(_URL_A)
    entry = reloaded.get(_URL_A)
    assert entry is not None
    assert entry["doc_id"] == "doc-x"
    assert entry["content_hash"] == "deadbeef"
    assert entry["node_ids"] == ["n1"]


def test_remove_makes_url_new_again(seeded_manifest):
    seeded_manifest.remove(_URL_A)
    assert seeded_manifest.is_new(_URL_A)


def test_corrupt_manifest_falls_back_to_empty(tmp_path):
    (tmp_path / "manifest.json").write_text("{invalid json}")
    m = Manifest(tmp_path)
    assert m.is_new(_URL_A)


def test_empty_manifest_file_falls_back_to_empty(tmp_path):
    (tmp_path / "manifest.json").write_text("")
    m = Manifest(tmp_path)
    assert m.is_new(_URL_A)


def test_save_creates_parent_dirs(tmp_path):
    nested = tmp_path / "deep" / "nested"
    m = Manifest(nested)
    m.record(
        url=_URL_A,
        content_hash="x",
        doc_id="d",
        node_ids=[],
        entity_kind="product",
    )
    m.save()
    assert (nested / "manifest.json").exists()
    data = json.loads((nested / "manifest.json").read_text())
    assert _URL_A in data
