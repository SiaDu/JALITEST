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

