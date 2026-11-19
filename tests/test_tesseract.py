import asyncio
from pathlib import Path

import pytest

from app.readers.image_reader import lines_from_analyze_result, read_image
from app.readers.tesseract_reader import (
    find_tesseract,
    join_wrapped,
    logical_order,
    parse_tsv,
    reads_agree,
    to_analyze_result,
)
from tests.conftest import make_settings

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def word(text, left, conf=95.0, top=10, width=40, line=1):
    return {"text": text, "left": left, "top": top, "right": left + width, "bottom": top + 20, "conf": conf}


def tsv(rows):
    lines = [HEADER]
    for i, (text, left, conf, line) in enumerate(rows, 1):
        lines.append(f"5\t1\t1\t1\t{line}\t{i}\t{left}\t{line * 30}\t40\t20\t{conf}\t{text}")
    return "\n".join(lines)


def test_latin_run_inside_arabic_line_is_put_back_in_reading_order():
    rows = [("حساب", 900, 95, 1), ("ماجد", 840, 95, 1), ("9001", 680, 95, 1), ("0000", 620, 95, 1), ("0088", 560, 95, 1), ("SA96", 500, 95, 1)]
    line = parse_tsv(tsv(rows))[0]
    assert [w["text"] for w in line] == ["حساب", "ماجد", "SA96", "0088", "0000", "9001"]


def test_leading_period_moves_to_end_of_number():
    line = logical_order([word("اتصلوا", 900), word(".0500000032", 700)])
    assert [w["text"] for w in line] == ["اتصلوا", "0500000032."]


def test_low_confidence_latin_word_is_treated_as_arabic_position():
    line = logical_order([word("على", 900), word("SA85", 700), word("pawl", 640, conf=60), word("نورة", 560)])
    assert [w["text"] for w in line] == ["على", "SA85", "pawl", "نورة"]


def test_reads_agree_ignores_punctuation_and_allows_digit_to_letter_fix():
    assert reads_agree([word("0500000011,", 0)], [word("<0500000011", 0)])
    assert reads_agree([word("5869", 0)], [word("SA69", 0)])
    assert not reads_agree([word("4,500", 0)], [word("4,200", 0)])
    assert not reads_agree([word("0163", 0)], [word("163", 0)])


def test_full_width_lines_without_end_punctuation_are_joined():
    full = [word("مكتب", 860, width=100), word("مرفأ", 100, width=100)]
    rest = [word("السكينة", 860, width=100), word("للاستقدام.", 600, width=100)]
    short = [word("جوال", 860, width=100)]
    assert len(join_wrapped([full, rest, short])) == 2
    ended = [word("مكتب", 860, width=100), word("مساند.", 100, width=100)]
    assert len(join_wrapped([ended, short])) == 2


def test_analyze_result_shape_feeds_existing_line_builder():
    result = to_analyze_result([[word("جوال", 900, conf=90), word("0500000011", 700, conf=40)]])
    lines = lines_from_analyze_result(result)
    assert lines[0].text == "جوال 0500000011"
    assert lines[0].source == "ocr"
    assert min(lines[0].char_confidence) == pytest.approx(0.4)


@pytest.mark.team_images
def test_missing_language_or_binary_is_reported():
    settings = make_settings(ocr_provider="tesseract", tesseract_cmd="Z:/no/such/tesseract.exe")
    data = Path("samples/team_images/T01.png").read_bytes()
    from app.readers.base import ReadError

    with pytest.raises(ReadError) as error:
        asyncio.run(read_image(data, settings))
    assert error.value.code == "ocr_not_configured"


TESSDATA = Path.home() / "tessdata"


@pytest.mark.team_images
@pytest.mark.skipif(find_tesseract("") is None or not (TESSDATA / "ara.traineddata").exists(), reason="Tesseract + ara غير مثبت")
def test_real_tesseract_reads_phone_and_iban():
    settings = make_settings(ocr_provider="tesseract", tesseract_tessdata_dir=str(TESSDATA))
    read = asyncio.run(read_image(Path("samples/team_images/T07.png").read_bytes(), settings))
    text = " ".join(line.text for line in read.lines)
    assert "0509990101" in text
    assert "SA96 0088 0000 0000 0000 9001" in text
