import re
import unicodedata
from dataclasses import dataclass

DIGIT_MAP = {chr(0x0660 + i): str(i) for i in range(10)} | {chr(0x06F0 + i): str(i) for i in range(10)}
INVISIBLE = set(
    "​‌‍‎‏‪‫‬‭‮⁦⁧⁨⁩﻿ـ"
)
SPACES = set("     ")
PUNCT_MAP = {"＋": "+", "٫": ".", "٬": ",", "−": "-", "–": "-", "—": "-"}


@dataclass
class NormalizedText:
    original: str
    text: str
    index: list[int]

    def raw(self, start: int, end: int) -> str:
        if start >= end:
            return ""
        return self.original[self.index[start] : self.index[end - 1] + 1]

    def original_positions(self, start: int, end: int) -> range:
        if start >= end:
            return range(0)
        return range(self.index[start], self.index[end - 1] + 1)


def normalize_text(text: str) -> NormalizedText:
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        if ch in INVISIBLE:
            continue
        for c in unicodedata.normalize("NFKC", DIGIT_MAP.get(ch, ch)):
            c = DIGIT_MAP.get(c, c)
            c = PUNCT_MAP.get(c, c)
            if c in SPACES:
                c = " "
            out.append(c)
            index.append(i)
    return NormalizedText(original=text, text="".join(out), index=index)


def clean_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def to_ascii_digits(text: str) -> str:
    return normalize_text(text).text


def mask(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        for k in range(start, end):
            chars[k] = " "
    return "".join(chars)
