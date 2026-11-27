"""lingual-audit: Bilingual UI inventory, tiered hybrid RAG audit, and Excel delivery."""

from lingual_audit.contracts import (
    CONTRACT_VERSION,
    EXCEL_COLUMNS,
    EXIT_CODES,
    VERDICT_STYLES,
)

__all__ = [
    "CONTRACT_VERSION",
    "EXCEL_COLUMNS",
    "EXIT_CODES",
    "VERDICT_STYLES",
]
__version__ = "0.1.0"
