"""Pydantic v2 data models for the RAG prototype (see SPEC.md §4).

Design principle for the domain types (`Product` / `CaseStudy`): only identity and
filter keys are typed fields; the remaining specifications go into a free-form
`dict[str, Any]` bag, because spec items vary widely per product category while
identifiers stay stable.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# --- Common ---------------------------------------------------------------


class DocType(str, Enum):
    PRODUCT = "product"
    CASE = "case"


class QueryPattern(str, Enum):
    CASE_SEARCH = "A"
    PRODUCT_SEARCH = "B"
    HYBRID = "C"


class QueryAnalysis(BaseModel):
    """LLM structured-output result; drives pattern routing and hallucination control."""

    pattern: QueryPattern
    product_hints: list[str]  # e.g. ["P3268-LVE"]
    industry_hints: list[str]  # e.g. ["retail", "factory"]
    spec_hints: list[str]  # e.g. ["IP66", "FOV>120"]
    rewritten_query: str  # query-rewriting result


class Citation(BaseModel):
    doc_type: DocType
    title: str
    source: str  # URL
    snippet: str


class RetrievedChunk(BaseModel):
    text: str
    score: float
    citation: Citation


# --- Domain types (extracted by the LLM at ingest time) -------------------


class ProductCategory(str, Enum):
    NETWORK_CAMERA = "network_camera"
    NETWORK_SPEAKER = "network_speaker"
    RADAR = "radar"
    ACCESS_CONTROL = "access_control"


# The `Field(description=...)` text is not just documentation: it is emitted into
# the JSON schema these models expose to the LLM's function-calling structured
# output at ingest time, so it directly steers extraction quality. The bag and
# optional fields carry defaults so a partial LLM extraction still validates.


class Product(BaseModel):
    model_name: str = Field(
        description="Full model name exactly as printed on the page, e.g. 'AXIS Q3558-LVE'."
    )
    category: ProductCategory = Field(description="Coarse product class, used for filtering.")
    subcategory: str | None = Field(
        default=None,
        description="Camera body style (fixed / dome / ptz / bullet / modular …), or null.",
    )
    specs: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Free-form key/value bag of specifications — ip_rating, resolution, "
            "fov_horizontal_deg, operating_temp_c, features, etc. Populate it richly."
        ),
    )
    source_url: str = Field(default="", description="URL of the product page.")
    raw_summary: str = Field(
        default="",
        description="2-3 sentence plain-English description of what the product is and does.",
    )


class CaseStudy(BaseModel):
    title: str = Field(description="Headline of the customer story.")
    industry: str = Field(
        description="Short industry label: retail / factory / parking / transportation / education …"  # noqa: E501
    )
    customer: str | None = Field(
        default=None, description="Customer organisation name, or null if not stated."
    )
    region: str | None = Field(default=None, description="Country or region, or null.")
    deployment_year: int | None = Field(
        default=None, description="Four-digit year the deployment occurred, or null."
    )
    referenced_models: list[str] = Field(
        default_factory=list,
        description="Axis model names mentioned in the story (Pattern C link).",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Free-form key/value bag — challenges, decision_factors, outcomes, "
            "solution. Populate it richly."
        ),
    )
    source_url: str = Field(default="", description="URL of the customer story page.")
    raw_summary: str = Field(
        default="", description="2-3 sentence plain-English summary of the story."
    )


# --- Final output ---------------------------------------------------------


class SourceItem(BaseModel):
    title: str
    doc_type: DocType
    source: str
    reason: str = Field(
        description=(
            "One-sentence reason (under 25 words) explaining why this source was "
            "relevant to the user's query."
        )
    )


class SourceReasons(BaseModel):
    reasons: list[str] = Field(
        description=(
            "One reason string per source, in the same order as the sources listed. "
            "Each reason must be a single sentence under 25 words."
        )
    )


class AnswerBundle(BaseModel):
    answer_markdown: str
    citations: list[Citation]
    used_pattern: QueryPattern
    source_items: list[SourceItem] = Field(default_factory=list)
    # optional, filled when structured comparison is needed (UI renders as a table)
    products_compared: list[Product] | None = None
    cases_compared: list[CaseStudy] | None = None
