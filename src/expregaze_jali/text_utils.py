from __future__ import annotations

import re
import unicodedata

QUOTE_AND_PUNCT_TRANSLATION = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "–": "-",
        "—": "-",
        "…": "...",
    }
)


def normalize_word(word: str) -> str:
    text = unicodedata.normalize("NFKC", word)
    text = text.translate(QUOTE_AND_PUNCT_TRANSLATION).strip()
    text = text.strip("\"'").lower()
    return re.sub(r"^[^\w]+|[^\w]+$", "", text)


def match_anchor_word_sequence(
    anchor_text: str, actual_words: list[str], start_index: int,
) -> int | None:
    """Strictly align one canonical anchor to one or more hyphenated words.

    Ordinary word matching remains exact under ``normalize_word``.  The only
    fallback joins a bounded number of lexical fragments for an anchor whose
    normalized form itself contains a hyphen.
    """
    expected = normalize_word(anchor_text)
    if not expected or start_index < 0 or start_index >= len(actual_words):
        return None
    if expected == normalize_word(actual_words[start_index]):
        return 1
    if "-" not in expected:
        return None
    expected_key = expected.replace("-", "")
    combined = ""
    maximum_fragments = expected.count("-") + 1
    for consumed in range(1, maximum_fragments + 1):
        index = start_index + consumed - 1
        if index >= len(actual_words):
            break
        combined += normalize_word(actual_words[index]).replace("-", "")
        if combined == expected_key:
            return consumed
        if not expected_key.startswith(combined):
            return None
    return None


def iter_word_tokens(text: str) -> list[dict]:
    tokens: list[dict] = []
    for match in re.finditer(r"[\w]+(?:'[\w]+)?", text, flags=re.UNICODE):
        token = match.group(0)
        norm = normalize_word(token)
        if norm:
            tokens.append(
                {
                    "text": token,
                    "norm": norm,
                    "start": match.start(),
                    "end": match.end(),
                }
            )
    return tokens

