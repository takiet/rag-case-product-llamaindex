# CLAUDE.md

## Project Overview

This project is a prototype AI assistant for product and deployment case study search.
The assistant is designed to provide high-quality answers to customer and partner inquiries regarding products, technical specifications, and real-world deployment examples.

## Target Domains
### Devices
- Network cameras
- Network speakers
- Radar systems
- Access control systems

### Vendor
- Axis Communications

## Supported Query Patterns
### Pattern A: Case Study Search
Users want to explore deployment examples, industry trends, and common characteristics across multiple case studies.

#### Typical Tasks
- Search deployment case studies
- Answer follow-up questions about deployments
- Analyze trends across multiple cases
- Extract common requirements or specifications

#### Example Queries
- "Show retail case studies related to checkout monitoring."
- "Why was this product selected?"
- "Are there examples where self-checkout issues were reduced?"
- "Show deployment examples for nighttime parking lot surveillance."
- "What trends have emerged in retail deployments over the past 5 years?"

### Pattern B: Product Search
Users want detailed product information, specifications, or comparisons.

#### Typical Tasks
- Retrieve specifications for a specific model
- Search products by capability or specification
- Compare multiple products in the same category

#### Example Queries
- "List cameras with high waterproof and dustproof resistance."
- "What is the horizontal field of view of Model-X?"
- "Which 4K cameras support outdoor installation?"
- "What specifications are shared by Model-X, Model-Y, and Model-Z?"

### Pattern C: Hybrid Search
Users want to connect products and deployment case studies.

#### Typical Tasks
- Identify products used in specific deployments
- Find environments suitable for products with certain specifications
- Recommend products based on common requirements observed across deployments

#### Example Queries
- "What environments use products with IP66 and IP67 ratings?"
- "What operating temperatures are common for cameras used in factories?"
- "Summarize retail deployments using modular cameras with a field of view greater than 120 degrees."
- "Why were models with strong backlight compensation selected?"

## Directory Structure
 .
├── .claude
│   ├── agents/  # agents
│   ├── plan/    # plan documents
│   └── skills/  # skills
├── .gitignore
├── .python-version
├── CLAUDE.md
├── SPEC.md      # Software Specification document
├── docs         # Architecture documents, etc
├── pyproject.toml
└── README.md

## Technical Stack
### Environment
- Python 3.13
- uv

### Code Quality
- ruff
- pytest

### Frameworks
- LlamaIndex
- MarkItDown

### Data Modeling
- Pydantic

### RAG Techniques
- Contextual retrieval
- Cross-encoder reranking

### Models
- Generation model: `gpt-4o-mini`
- Embedding model: `text-embedding-3-small`

### UI
- Chainlit

## Development Guidelines

### General Principles
- Prioritize factual and grounded responses with citation and source linking
- Avoid hallucinations and unsupported assumptions.
- Prefer structured outputs when comparing products or deployments.
- Keep responses concise unless detailed analysis is requested.
- Make progress (e.g. rewrote prompts, retrieved chunks, etc) visible in Chainlit UI
- Follow the best practices and framework design principles, Do not dirty hacks

### Retrieval Strategy
- Use contextual retrieval to improve semantic relevance.
- Apply reranking after initial retrieval.
- Prioritize recent and high-quality deployment examples when available.

### Product Information
- Normalize specifications before comparison.
- Treat vendor specifications as authoritative when conflicts occur.
- Clearly distinguish inferred information from explicit specifications.

### Case Study Analysis
- Extract:
  - deployment environment
  - customer challenges
  - selected products
  - key decision factors
  - outcomes and benefits

- Support multi-document trend analysis when multiple case studies are retrieved.

### Hybrid Queries
For queries involving both products and deployments:
- Link deployment requirements to product specifications and vice versa
- Explain why a product was suitable for a specific environment.
- Identify recurring specification patterns across deployments.

## Expected Future Enhancements
- Multi-vendor support
- Metadata filtering
- Structured product comparison UI
- Evaluation pipeline for retrieval quality
- Benchmark dataset for query accuracy

## Workflow Orchestration
### 1. Plan Node Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately – don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop
- After ANY correction from the user: update `plan/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes – don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests – then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management
1. **Plan First**: Write plan to `plan/todo.md` with checkable items based on `SPEC.md`
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go
4. **Explain Changes**: High-level summary at each step
5. **Document Results**: Add review section to `plan/todo.md`
6. **Capture Lessons**: Update `plan/lessons.md` after corrections

## Core Principles

- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what's necessary. Avoid introducing bugs.
- **Minimal UI framework dependencies**: Separate application logic from UI (e.g. separete agent workflow states from ui states)

## Language Policy

- User interactions and conversations may be conducted in Japanese.
- All generated files, documents, source code, comments, specifications, and outputs must be written in English.
