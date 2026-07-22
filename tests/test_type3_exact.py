from pathlib import Path

import pikepdf

from editnum.models import NormalizedRect, PageRecognition
from editnum.type3_exact import (
    NEW_GLYPH_CODES,
    _contents,
    _shown_codes,
    replace_exact_type3,
)


SOURCE_TEXT = "0123456789BJKLQR-"


def _create_type3_fixture(path: Path) -> None:
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(900, 900))
    glyph_names = [pikepdf.Name(f"/glyph{index}") for index in range(19)]
    char_procs = pikepdf.Dictionary()
    for index, name in enumerate(glyph_names):
        char_procs[name] = pikepdf.Stream(
            pdf,
            (
                "1 0 -10 -10 10 10 d1\n"
                f"{index} 0 m\n"
                f"{index} {index + 1} l\n"
                "S\n"
            ).encode("ascii"),
        )

    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type3,
            FontBBox=pikepdf.Array([-10, -10, 20, 20]),
            FontMatrix=pikepdf.Array([1, 0, 0, 1, 0, 0]),
            CharProcs=char_procs,
            Encoding=pikepdf.Dictionary(
                Type=pikepdf.Name.Encoding,
                Differences=pikepdf.Array([0, *glyph_names]),
            ),
            FirstChar=0,
            LastChar=18,
            Widths=pikepdf.Array([1] * 19),
            Resources=pikepdf.Dictionary(),
        )
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))

    phrase_codes = [0, *range(1, 18), 18]
    title_codes = list(range(1, 18))

    def text_run(codes: list[int]) -> list[str]:
        lines: list[str] = []
        for index, code in enumerate(codes):
            lines.append(f"<{code:02x}> Tj")
            if index != len(codes) - 1:
                lines.append("0.35 0 TD")
        return lines

    content = [
        "BT",
        "/F1 1 Tf",
        "0 8.76 -8.762 0 31.4673 69.7771 Tm",
        *text_run(phrase_codes),
        "0 9.8776 -9.8889 0 531.2477 761.1565 Tm",
        *text_run(title_codes),
        "ET",
    ]
    page.Contents = pikepdf.Stream(pdf, ("\n".join(content) + "\n").encode("ascii"))
    pdf.save(path)


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


def test_global_replacement_preserves_surrounding_type3_sentence(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    _create_type3_fixture(source)
    selection = NormalizedRect(0.80, 0.80, 0.95, 0.95)
    recognitions = [PageRecognition(1, SOURCE_TEXT, selection)]
    target = "210"

    result = replace_exact_type3(
        source,
        output,
        recognitions,
        {SOURCE_TEXT: target},
        global_search=True,
    )

    assert result.fixed_replacements == 1
    assert result.global_replacements == 1
    original = pikepdf.open(source)
    replaced = pikepdf.open(output)
    try:
        old_matrix, old_codes = _longest_text_block(original.pages[0])
        new_matrix, new_codes = _longest_text_block(replaced.pages[0])
        assert new_matrix == old_matrix
        assert new_codes[:1] == old_codes[:1]
        assert new_codes[1 + len(target) :] == old_codes[18:]
        assert new_codes[1 : 1 + len(target)] == [NEW_GLYPH_CODES[char] for char in target]
    finally:
        original.close()
        replaced.close()
