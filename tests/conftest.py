"""Shared pytest configuration.

LlamaIndex currently emits UnsupportedFieldAttributeWarning from its own
internal Pydantic Field() usage (node_parser/interface.py and agent code).
Suppress these third-party warnings so the test output stays readable.
"""

import warnings


def pytest_configure(config):
    warnings.filterwarnings(
        "ignore",
        message="The 'validate_default' attribute",
        category=UserWarning,
    )
