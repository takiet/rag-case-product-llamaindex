"""Entity extraction via gpt-4o-mini structured output (SPEC §4.4, §5.2 step 3)."""

import json
from typing import TypeVar

from llama_index.core.schema import Document
from llama_index.llms.openai import OpenAI
from pydantic import BaseModel

from rag_case_products.config import LLM_MODEL
from rag_case_products.ingest.node_builder import page_content
from rag_case_products.models import CaseStudy, DocType, Product

_PRODUCT_PROMPT = """\
You are a structured data extractor for Axis Communications product pages.

Extract a single JSON object that matches the Product schema from the page below.
Rules:
- model_name: full model name as printed on the page (e.g. "AXIS Q3558-LVE").
- category: one of "network_camera", "network_speaker", "radar", "access_control".
- subcategory: camera body style such as "fixed", "dome", "ptz", "bullet", "modular" — or null.
- specs: key/value bag. All values are free-form strings copied verbatim from the page,
  including ranges and units (e.g. fov_horizontal_deg may be "104.0 - 48.9 °",
  operating_temp_c may be "-40 °C to 60 °C", resolution may be "3840x2160").
  Never set a spec to null just because its value is a range or non-numeric —
  record it exactly as printed.
  Always populate fov_horizontal_deg (horizontal field of view) and operating_temp_c
  (operating temperature) when the information appears on the page, even as a range string.
  Other common keys: ip_rating, resolution, features.
- source_url: the URL of the page (provided below).
- raw_summary: 2–3 sentence plain-English description of what the product is and does.

SOURCE URL: {url}

PAGE TEXT (truncated to first 6000 chars):
{text}
"""

_CASE_PROMPT = """\
You are a structured data extractor for Axis Communications customer story pages.

Extract a single JSON object that matches the CaseStudy schema from the page below.
Rules:
- title: the headline of the customer story.
- industry: short label such as "retail", "factory", "parking", "transportation", "education", etc.
- customer: organisation name, or null if not stated.
- region: country or region, or null.
- deployment_year: four-digit year the deployment occurred, or null.
- referenced_models: list of Axis model names mentioned (e.g. ["AXIS Q62-CE PTZ Camera"]).
- details: free-form bag with keys such as "challenges", "decision_factors", "outcomes", "solution".
- source_url: the URL of the page (provided below).
- raw_summary: 2–3 sentence plain-English summary of the story.

SOURCE URL: {url}

PAGE TEXT (truncated to first 6000 chars):
{text}
"""

_PAGE_TEXT_LIMIT = 6000

# JSON mode guarantees a syntactically valid JSON object reply. Function-calling
# structured output cannot express the open-ended specs/details bag as fillable
# slots (it returns them empty); a prompt-described schema + JSON mode can.
_llm = OpenAI(
    model=LLM_MODEL,
    temperature=0,
    additional_kwargs={"response_format": {"type": "json_object"}},
)

_M = TypeVar("_M", bound=BaseModel)


def _extract(doc: Document, prompt_template: str, model_cls: type[_M]) -> _M:
    url = doc.metadata.get("source", "")
    prompt = prompt_template.format(url=url, text=page_content(doc.text)[:_PAGE_TEXT_LIMIT])
    data = json.loads(_llm.complete(prompt).text)
    entity = model_cls.model_validate(data)
    entity.source_url = url
    return entity


def extract_product(doc: Document) -> Product:
    entity = _extract(doc, _PRODUCT_PROMPT, Product)
    doc.metadata["entity"] = entity.model_dump_json()
    doc.metadata["model_name"] = entity.model_name
    doc.metadata["category"] = entity.category.value
    doc.metadata["subcategory"] = entity.subcategory or ""
    doc.metadata["source_url"] = entity.source_url
    return entity


def extract_case(doc: Document) -> CaseStudy:
    entity = _extract(doc, _CASE_PROMPT, CaseStudy)
    doc.metadata["entity"] = entity.model_dump_json()
    doc.metadata["title"] = entity.title
    doc.metadata["industry"] = entity.industry
    doc.metadata["customer"] = entity.customer or ""
    doc.metadata["region"] = entity.region or ""
    doc.metadata["deployment_year"] = str(entity.deployment_year) if entity.deployment_year else ""
    doc.metadata["source_url"] = entity.source_url
    return entity


def extract_entity(doc: Document) -> Product | CaseStudy:
    doc_type = DocType(doc.metadata.get("doc_type", DocType.PRODUCT.value))
    if doc_type == DocType.PRODUCT:
        return extract_product(doc)
    return extract_case(doc)
