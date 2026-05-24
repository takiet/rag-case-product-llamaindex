from pathlib import Path

from llama_index.core import StorageContext, VectorStoreIndex, load_index_from_storage

from rag_case_products.config import CASES_STORAGE, PRODUCTS_STORAGE


def _load_index(path: Path) -> VectorStoreIndex:
    if not (path / "docstore.json").exists():
        raise FileNotFoundError(f"Index not found at {path}; run the ingest CLI first")
    return load_index_from_storage(StorageContext.from_defaults(persist_dir=str(path)))


def load_products_index() -> VectorStoreIndex:
    return _load_index(PRODUCTS_STORAGE)


def load_cases_index() -> VectorStoreIndex:
    return _load_index(CASES_STORAGE)
