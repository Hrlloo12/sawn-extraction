from dataclasses import dataclass, field


@dataclass
class Line:
    text: str
    source: str
    section: str = "unknown"
    line_no: int | None = None
    bbox: list[float] | None = None
    confidence: float | None = None
    char_confidence: list[float] | None = None


@dataclass
class Link:
    href: str
    text: str
    section: str = "unknown"
    source: str = "page_link"


@dataclass
class ReadResult:
    input_type: str
    lines: list[Line] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    input_url: str | None = None
    final_url: str | None = None


class ReadError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        suggestion: str | None = None,
        final_url: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggestion = suggestion
        self.final_url = final_url
