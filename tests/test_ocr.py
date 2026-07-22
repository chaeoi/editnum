from editnum.models import OcrSpan
from editnum.ocr import find_mapping_matches, primary_text


def test_primary_text_chooses_longest_line() -> None:
    spans = [
        OcrSpan("比例", 0.99, (0, 30, 20, 40)),
        OcrSpan("07-0326", 0.97, (40, 0, 90, 12)),
        OcrSpan("DU01A", 0.96, (92, 0, 130, 12)),
    ]
    text, confidence, box = primary_text(spans)
    assert text == "07-0326DU01A"
    assert confidence > 0.96
    assert box == (40, 0, 130, 12)


def test_global_match_can_span_adjacent_ocr_boxes() -> None:
    spans = [
        OcrSpan("07-0326", 0.98, (10, 10, 60, 20)),
        OcrSpan("DU01A", 0.97, (61, 10, 100, 20)),
        OcrSpan("unrelated", 0.99, (10, 40, 50, 50)),
    ]
    matches = find_mapping_matches(spans, {"07-0326DU01A"})
    assert len(matches) == 1
    assert matches[0].key == "07-0326DU01A"
    assert matches[0].box == (10, 10, 100, 20)
    assert matches[0].line_text == "07-0326DU01A"


def test_global_match_can_be_inside_a_longer_ocr_box() -> None:
    spans = [OcrSpan("REF:07-0326DU01A/REV", 0.97, (0, 0, 210, 20))]
    matches = find_mapping_matches(spans, {"07-0326DU01A"})
    assert len(matches) == 1
    assert matches[0].key == "07-0326DU01A"
    assert matches[0].line_text == "REF:07-0326DU01A/REV"
