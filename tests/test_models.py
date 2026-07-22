from drawing_number_tool.models import (
    NormalizedRect,
    PageRecognition,
    ProcessOptions,
    build_mapping,
    clean_ocr_text,
)


def recognition(page: int, source: str) -> PageRecognition:
    return PageRecognition(page, source, 0.99, NormalizedRect(0.8, 0.8, 0.9, 0.9))


def test_duplicate_sources_share_sequence_without_incrementing() -> None:
    entries, lookup = build_mapping(
        [recognition(1, "A-01"), recognition(2, "A-01"), recognition(3, "B-02")],
        ProcessOptions(template="NEW-{seq:03d}"),
    )

    assert [(entry.source, entry.target, entry.pages) for entry in entries] == [
        ("A-01", "NEW-001", (1, 2)),
        ("B-02", "NEW-002", (3,)),
    ]
    assert lookup == {"A-01": "NEW-001", "B-02": "NEW-002"}


def test_ocr_text_normalizes_width_whitespace_and_dashes() -> None:
    assert clean_ocr_text(" ０７‑0326 A ") == "07-0326A"


def test_template_must_change_with_sequence() -> None:
    options = ProcessOptions(template="constant")
    try:
        options.validate()
    except ValueError as exc:
        assert "{seq}" in str(exc)
    else:
        raise AssertionError("constant template should be rejected")
