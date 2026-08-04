from editnum.models import (
    NormalizedRect,
    PageRecognition,
    ProcessOptions,
    build_mapping,
    clean_ocr_text,
)


def recognition(page: int, source: str) -> PageRecognition:
    return PageRecognition(page, source, NormalizedRect(0.8, 0.8, 0.9, 0.9))


def test_duplicate_sources_share_sequence_without_incrementing() -> None:
    entries, lookup = build_mapping(
        [recognition(1, "A-01"), recognition(2, "A-01"), recognition(3, "B-02")],
        ProcessOptions(fixed_prefix="NEW-", fixed_suffix="", sequence_digits=3),
    )

    assert [(entry.source, entry.target, entry.pages) for entry in entries] == [
        ("A-01", "NEW-001", (1, 2)),
        ("B-02", "NEW-002", (3,)),
    ]
    assert lookup == {"A-01": "NEW-001", "B-02": "NEW-002"}


def test_ocr_text_normalizes_width_whitespace_and_dashes() -> None:
    assert clean_ocr_text(" ０７‑0326 A ") == "07-0326A"


def test_all_numbering_parts_are_individually_optional() -> None:
    without_prefix = ProcessOptions(fixed_prefix="", fixed_suffix="-END", sequence_digits=2)
    without_suffix = ProcessOptions(fixed_prefix="DOC-", fixed_suffix="", sequence_digits=1)
    without_sequence = ProcessOptions(
        fixed_prefix="FIXED", fixed_suffix="-END", start_sequence=None
    )

    assert without_prefix.number_for(1) == "01-END"
    assert without_suffix.number_for(1) == "DOC-1"
    assert without_sequence.number_for(100) == "FIXED-END"


def test_all_numbering_parts_can_be_empty_to_clear_the_old_number() -> None:
    options = ProcessOptions(fixed_prefix="", fixed_suffix="", start_sequence=None)
    assert options.number_for(1) == ""


def test_document_wide_replacement_is_opt_in() -> None:
    assert not ProcessOptions().global_search


def test_default_numbering_uses_only_the_incrementing_part() -> None:
    options = ProcessOptions()

    assert options.fixed_prefix == ""
    assert options.fixed_suffix == ""
    assert options.number_for(1) == "001"


def test_fixed_and_global_font_sizes_are_optional_and_independent() -> None:
    options = ProcessOptions(fixed_font_size=16, global_font_size=9.5)
    options.validate()
    assert options.fixed_font_size == 16
    assert options.global_font_size == 9.5


def test_font_sizes_must_be_in_a_practical_range() -> None:
    for field in ("fixed_font_size", "global_font_size"):
        options = ProcessOptions(**{field: 0.5})
        try:
            options.validate()
        except ValueError as exc:
            assert "字号" in str(exc)
        else:
            raise AssertionError("invalid font size should fail validation")
