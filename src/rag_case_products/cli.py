"""CLI entrypoint: python -m rag_case_products.cli ingest [FLAGS] (SPEC §5.7)."""

import argparse
import logging
import sys

from rag_case_products.ingest.pipeline import build_and_persist_indices


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rag_case_products.cli",
        description="Ingest pipeline for the RAG case-products prototype.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Ingest URLs into the vector store.")
    ingest.add_argument(
        "--source",
        choices=["products", "cases", "all"],
        default="all",
        help="Which source to ingest (default: all).",
    )
    ingest.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process at most the first N URLs per source.",
    )
    ingest.add_argument(
        "--force",
        action="store_true",
        help="Ignore the manifest and re-ingest all URLs.",
    )
    ingest.add_argument(
        "--dry-run",
        action="store_true",
        help="Print target URLs without fetching or writing anything.",
    )
    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "ingest":
        build_and_persist_indices(
            source=args.source,
            limit=args.limit,
            force=args.force,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
