"""Ingest orchestration: steps 1–7 from SPEC §5.2.

Public API used by cli.py:
    build_and_persist_indices(
        source: str = "all",
        limit: int | None = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> None
"""

import logging
from pathlib import Path

from llama_index.core import (
    Settings,
    StorageContext,
    VectorStoreIndex,
    load_index_from_storage,
)
from llama_index.embeddings.openai import OpenAIEmbedding

from rag_case_products.config import (
    CASES_STORAGE,
    CASES_URLS,
    EMBED_MODEL,
    PRODUCTS_STORAGE,
    PRODUCTS_URLS,
)
from rag_case_products.ingest.contextual import add_contextual_prefixes
from rag_case_products.ingest.entities import extract_entity
from rag_case_products.ingest.loaders import UrlLoader
from rag_case_products.ingest.manifest import Manifest
from rag_case_products.ingest.node_builder import build_nodes
from rag_case_products.models import DocType

logging.getLogger(__name__).addHandler(logging.NullHandler())
log = logging.getLogger(__name__)

_SOURCE_CONFIG: dict[str, tuple[Path, Path, DocType]] = {
    "products": (PRODUCTS_URLS, PRODUCTS_STORAGE, DocType.PRODUCT),
    "cases": (CASES_URLS, CASES_STORAGE, DocType.CASE),
}


def _read_urls(urls_file: Path, limit: int | None) -> list[str]:
    """Read non-comment, non-empty URLs from a urls.txt file, capped to limit."""
    urls = [
        line.strip()
        for line in urls_file.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if limit is not None:
        urls = urls[:limit]
    return urls


def _ingest_source(
    name: str,
    urls_file: Path,
    storage_dir: Path,
    doc_type: DocType,
    limit: int | None,
    force: bool,
    dry_run: bool,
    loader: UrlLoader,
) -> None:
    urls = _read_urls(urls_file, limit)
    manifest = Manifest(storage_dir)

    if force:
        to_process = urls
    else:
        to_process = [u for u in urls if manifest.is_new(u)]
        skipped = len(urls) - len(to_process)
        if skipped:
            log.info("[%s] skip (already ingested): %d URL(s)", name, skipped)

    if dry_run:
        log.info("[%s] dry-run — would process %d URL(s):", name, len(to_process))
        for url in to_process:
            log.info("  %s", url)
        return

    if not to_process:
        log.info("[%s] nothing to ingest", name)
        return

    # SimpleVectorStore persists as `default__vector_store.json`; `docstore.json`
    # is the namespace-independent marker that a prior index exists here.
    index_exists = (storage_dir / "docstore.json").exists()
    if index_exists:
        storage_context = StorageContext.from_defaults(persist_dir=str(storage_dir))
        index = load_index_from_storage(storage_context)
    else:
        storage_dir.mkdir(parents=True, exist_ok=True)
        storage_context = StorageContext.from_defaults()
        index = VectorStoreIndex(nodes=[], storage_context=storage_context)

    for url in to_process:
        log.info("[%s] ingesting: %s", name, url)
        try:
            if force:
                record = manifest.get(url)
                if record:
                    doc_id = record["doc_id"]
                    try:
                        index.delete_ref_doc(doc_id, delete_from_docstore=True)
                        log.info("[%s] deleted old nodes for doc_id=%s", name, doc_id)
                    except Exception:
                        log.warning("[%s] could not delete doc_id=%s (may not exist)", name, doc_id)

            doc = loader.load(url, doc_type)
            entity = extract_entity(doc)
            entity_kind = type(entity).__name__
            nodes = build_nodes(doc)
            add_contextual_prefixes(nodes)
            index.insert_nodes(nodes)

            # Persist before recording in the manifest so a crash between these
            # two writes leaves the URL unrecorded (re-ingested on next run) rather
            # than permanently skipped with nodes that were never persisted (B3).
            storage_context.persist(persist_dir=str(storage_dir))

            manifest.record(
                url=url,
                content_hash=doc.metadata["content_hash"],
                doc_id=doc.id_,
                node_ids=[n.node_id for n in nodes],
                entity_kind=entity_kind,
            )
            manifest.save()
            log.info("[%s] done: %d nodes from %s", name, len(nodes), url)
        except Exception as exc:
            log.error("[%s] failed to ingest %s: %s", name, url, exc)

    log.info("[%s] finished source: %s", name, storage_dir)


def build_and_persist_indices(
    source: str = "all",
    limit: int | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    """Orchestrate SPEC §5.2 steps 1–7 for the selected source(s).

    Args:
        source: "products", "cases", or "all".
        limit: cap each source's urls.txt to the first N URLs before the diff.
        force: re-ingest all URLs, deleting existing nodes first.
        dry_run: print the plan without fetching or writing anything.
    """
    Settings.embed_model = OpenAIEmbedding(model=EMBED_MODEL)
    names = ["products", "cases"] if source == "all" else [source]
    loader = UrlLoader()

    for name in names:
        urls_file, storage_dir, doc_type = _SOURCE_CONFIG[name]
        _ingest_source(
            name=name,
            urls_file=urls_file,
            storage_dir=storage_dir,
            doc_type=doc_type,
            limit=limit,
            force=force,
            dry_run=dry_run,
            loader=loader,
        )
