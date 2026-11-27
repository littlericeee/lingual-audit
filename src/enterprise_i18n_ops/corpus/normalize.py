"""Search normalization shared by corpus indexing and query construction."""

import re
import unicodedata

_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_HAN_RUN = re.compile(
    r"[\u3007\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\U00020000-\U0002a6df\U0002a700-\U0002ee5f"
    r"\U0002f800-\U0002fa1f\U00030000-\U0003347f]+"
)


def normalize_text(text: str) -> str:
    """Normalize a search view while leaving the source text to the caller."""
    return " ".join(unicodedata.normalize("NFKC", text).translate(_ASCII_LOWER).split())


def cjk_bigrams(text: str) -> str:
    """Return contiguous Han bigrams, or a unigram for an isolated character."""
    return " ".join(
        token
        for run in _HAN_RUN.findall(normalize_text(text))
        for token in ([run] if len(run) == 1 else (run[i : i + 2] for i in range(len(run) - 1)))
    )


def cjk_search_tokens(text: str) -> str:
    """Index contiguous bigrams first so phrase queries can match long substrings.

    Also index single characters from longer runs so one-character queries can match them.
    """
    bigrams = cjk_bigrams(text)
    characters = dict.fromkeys(
        char for run in _HAN_RUN.findall(normalize_text(text)) if len(run) > 1 for char in run
    )
    return " ".join(filter(None, (bigrams, " ".join(characters))))
