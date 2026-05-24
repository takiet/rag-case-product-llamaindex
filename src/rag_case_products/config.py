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
# Pipeline per tool call: retrieve `*_TOP_K` candidates → rerank → keep `RERANK_TOP_N`.
SIMILARITY_TOP_K = 10      # products: candidate pool before rerank
RERANK_TOP_N = 5           # nodes kept after rerank, shared by both tools
RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
# cases: MMR candidate pool — wider than products to surface diverse industries
CASES_MMR_TOP_K = 30
CASES_MMR_THRESHOLD = 0.5  # 1.0 = pure similarity, 0.0 = pure diversity

# -- Chunking --
CONTEXTUAL_SPLIT_THRESHOLD = 1500  # tokens; cascade SentenceSplitter only above this
SENTENCE_SPLITTER_CHUNK_SIZE = 1024
SENTENCE_SPLITTER_CHUNK_OVERLAP = 100

# -- Cache dirs --
PRODUCTS_RAW_DIR = PROJECT_ROOT / "data" / "products" / "raw"
PRODUCTS_PARSED_DIR = PROJECT_ROOT / "data" / "products" / "parsed"
CASES_RAW_DIR = PROJECT_ROOT / "data" / "cases" / "raw"
CASES_PARSED_DIR = PROJECT_ROOT / "data" / "cases" / "parsed"
