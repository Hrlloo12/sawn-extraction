from app.readers.base import Line, ReadResult


def read_text(value: str) -> ReadResult:
    lines = []
    for raw in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        text = raw.strip()
        if text:
            lines.append(Line(text=text, source="text", line_no=len(lines) + 1))
    return ReadResult(input_type="text", lines=lines)
