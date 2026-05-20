from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# -- Model names --
LLM_MODEL = "gpt-4o-mini"
EMBED_MODEL = "text-embedding-3-small"

# -- Storage paths --
PROJECT_ROOT = Path(__file__).parent.parent.parent
STORAGE_ROOT = PROJECT_ROOT / "storage"
PRODUCTS_STORAGE = STORAGE_ROOT / "products"
CASES_STORAGE = STORAGE_ROOT / "cases"
PRODUCTS_URLS = PROJECT_ROOT / "data" / "products" / "urls.txt"
CASES_URLS = PROJECT_ROOT / "data" / "cases" / "urls.txt"

# -- Retrieval --
SIMILARITY_TOP_K = 8
RERANK_TOP_N = 4
RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# -- Chunking --
CONTEXTUAL_SPLIT_THRESHOLD = 1500  # tokens; cascade SentenceSplitter only above this
SENTENCE_SPLITTER_CHUNK_SIZE = 1024
SENTENCE_SPLITTER_CHUNK_OVERLAP = 100
HIERARCHICAL_CHUNK_SIZES = [2048, 512, 128]

# -- PDF cache dirs --
PRODUCTS_RAW_DIR = PROJECT_ROOT / "data" / "products" / "raw"
PRODUCTS_PARSED_DIR = PROJECT_ROOT / "data" / "products" / "parsed"
