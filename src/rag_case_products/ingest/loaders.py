"""URL loader seam wrapping markitdown (SPEC §5.1, §5.2 step 2)."""

import hashlib
from datetime import datetime, timezone

from llama_index.core.schema import Document
from markitdown import MarkItDown

from rag_case_products.models import DocType

_converter = MarkItDown()


def _url_to_doc_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


class UrlLoader:
    def load(self, url: str, doc_type: DocType) -> Document:
        result = _converter.convert(url)
        content = result.text_content or ""
        title = result.title or url

        return Document(
            text=content,
            id_=_url_to_doc_id(url),
            metadata={
                "doc_type": doc_type.value,
                "source": url,
                "title": title,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            },
        )
