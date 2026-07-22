from pathlib import Path

import pikepdf

from drawing_number_tool.models import NormalizedRect, PageRecognition, ProcessOptions, build_mapping
from drawing_number_tool.type3_exact import _contents, _shown_codes, replace_exact_type3


SAMPLE_TITLES = (
    "07-0226AZ01B",
    "07-0226AZ02A",
    "07-0226AZ05A",
    "07-0226AZ17B",
    "07-0226AZ17B",
    "07-0226AZ28A",
    "07-0226AZ29A",
    "07-0324LE01A",
    "07-0324LE05A",
    "07-0324LE08A",
    "07-0324LE09A",
    "07-0324LE10A",
    "07-0324LE12A",
    "07-0325HR02A",
    "07-0325HV01B",
    "07-0325HV05A",
    "07-0325HV06B",
    "07-0325HV07B",
    "07-0325HV08B",
    "07-0325HV09B",
    "07-0325HV10B",
    "07-0325HV11A",
    "07-0325HV12B",
    "07-0325HV13B",
    "07-0325HV14B",
    "07-0325HV18B",
    "07-0325HV19B",
    "07-0325HV20A",
    "07-0325HV21A",
    "07-0325HV22A",
    "07-0325HV23B",
    "07-0325HV24B",
    "07-0325HV25B",
    "07-0325HV26A",
    "07-0325HV29C",
    "07-0326DU01A",
    "07-0326DU02A",
    "07-0326DU03A",
    "07-0326DU05A",
    "07-0326DU06A",
    "07-0326GR11A",
    "08-0226AZ05B",
    "08-0226DT01A",
)


def _longest_text_block(page) -> tuple[list[float], list[int]]:
    longest: tuple[list[float], list[int]] = ([], [])
    for stream in _contents(page):
        instructions = list(pikepdf.parse_content_stream(stream))
        for start, item in enumerate(instructions):
            if str(item.operator) != "Tm":
                continue
            end = start + 1
            while end < len(instructions) and str(instructions[end].operator) not in ("Tm", "ET"):
                end += 1
            codes = _shown_codes(instructions[start:end])
            if len(codes) > len(longest[1]):
                longest = ([float(value) for value in item.operands], codes)
    return longest


def test_sample_global_replacement_preserves_surrounding_type3_sentence(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "samples" / "图纸26072201.pdf"
    output = tmp_path / "output.pdf"
    selection = NormalizedRect(0.89, 0.85, 0.97, 0.92)
    recognitions = [
        PageRecognition(index, title, selection)
        for index, title in enumerate(SAMPLE_TITLES, 1)
    ]
    _, mapping = build_mapping(recognitions, ProcessOptions(global_search=True))

    result = replace_exact_type3(source, output, recognitions, mapping, global_search=True)

    assert result.fixed_replacements == 43
    assert result.global_replacements == 2
    original = pikepdf.open(source)
    replaced = pikepdf.open(output)
    try:
        for page_number, mapping_key in ((7, "07-0226AZ02A"), (40, "07-0326DU05A")):
            old_matrix, old_codes = _longest_text_block(original.pages[page_number - 1])
            new_matrix, new_codes = _longest_text_block(replaced.pages[page_number - 1])
            target = mapping[mapping_key]
            assert new_matrix == old_matrix
            assert new_codes[:5] == old_codes[:5]
            assert new_codes[5 + len(target) :] == old_codes[17:]
            assert all(code >= 200 for code in new_codes[5 : 5 + len(target)])
    finally:
        original.close()
        replaced.close()
