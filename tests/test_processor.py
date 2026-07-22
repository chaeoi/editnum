from pathlib import Path

import pymupdf

from drawing_number_tool.models import NormalizedRect, PageRecognition, ProcessOptions
from drawing_number_tool.processor import _replace_mapped_text, process_document


class EmptyOcr:
    def recognize(self, image):
        return []


def test_visual_fallback_replaces_fixed_and_global_text_and_writes_log(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    document = pymupdf.open()
    page = document.new_page(width=300, height=200)
    page.insert_text((170, 175), "OLD-01", fontsize=10)
    page.insert_text((30, 60), "OLD-01", fontsize=10)
    document.save(source)
    document.close()

    selection = NormalizedRect(0.50, 0.78, 0.98, 0.92)
    recognition = PageRecognition(
        page_number=1,
        source="OLD-01",
        text_rect=NormalizedRect(0.56, 0.80, 0.78, 0.90),
    )
    report = process_document(
        source,
        output,
        selection,
        [recognition],
        ProcessOptions(
            fixed_prefix="NEW-", fixed_suffix="", sequence_digits=3, global_search=True
        ),
        ocr=EmptyOcr(),
    )

    assert report.fixed_replacements == 1
    assert report.global_replacements == 1
    assert report.exact_font_preserved
    assert not report.warnings
    assert output.exists()
    assert output.with_suffix(".log").exists()
    assert "OLD-01 -> NEW-001" in output.with_suffix(".log").read_text(encoding="utf-8-sig")
    result = pymupdf.open(output)
    text = "".join(page.get_text() for page in result)
    result.close()
    assert "OLD-01" not in text
    assert text.count("NEW-001") == 2


def test_global_text_replacement_reflows_the_complete_line() -> None:
    assert _replace_mapped_text(
        "REF: OLD-01 / REV A", {"OLD-01": "NEW-001"}
    ) == "REF: NEW-001 / REV A"
    assert _replace_mapped_text(
        "OLD - 01 与 OLD-01", {"OLD-01": "NEW-001"}
    ) == "NEW-001 与 NEW-001"
    assert _replace_mapped_text("图号 OLD-01", {"OLD-01": ""}) == "图号 "


def test_document_wide_replacement_is_not_run_unless_selected(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    document = pymupdf.open()
    page = document.new_page(width=300, height=200)
    page.insert_text((170, 175), "OLD-01", fontsize=10)
    page.insert_text((30, 60), "OLD-01", fontsize=10)
    document.save(source)
    document.close()

    selection = NormalizedRect(0.50, 0.78, 0.98, 0.92)
    recognition = PageRecognition(
        page_number=1,
        source="OLD-01",
        text_rect=NormalizedRect(0.56, 0.80, 0.78, 0.90),
    )
    report = process_document(
        source,
        output,
        selection,
        [recognition],
        ProcessOptions(fixed_prefix="NEW-", fixed_suffix="", sequence_digits=3),
        ocr=EmptyOcr(),
    )

    assert report.global_replacements == 0
    result = pymupdf.open(output)
    text = "".join(page.get_text() for page in result)
    result.close()
    assert text.count("OLD-01") == 1
    assert text.count("NEW-001") == 1
