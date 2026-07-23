from pathlib import Path

import pikepdf

from editnum.models import NormalizedRect, PageRecognition
from editnum.type0_exact import replace_exact_type0


TO_UNICODE = b"""/CIDInit /ProcSet findresource begin
12 dict begin begincmap
1 begincodespacerange <0000> <FFFF> endcodespacerange
5 beginbfchar
<0010> <002D>
<0013> <0030>
<0014> <0031>
<0015> <0032>
<0024> <0041>
endbfchar
endcmap CMapName currentdict /CMap defineresource pop end end
"""


def _font(pdf: pikepdf.Pdf, cmap: bytes, base_name: str = "/ABCDEF+Test") -> object:
    descendant = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.CIDFontType2,
            DW=1000,
            W=pikepdf.Array(
                [
                    0x10,
                    pikepdf.Array([500]),
                    0x13,
                    0x15,
                    350,
                    0x24,
                    pikepdf.Array([350]),
                ]
            ),
        )
    )
    return pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type0,
            BaseFont=pikepdf.Name(base_name),
            Encoding=pikepdf.Name("/Identity-H"),
            DescendantFonts=pikepdf.Array([descendant]),
            ToUnicode=pdf.make_stream(cmap),
        )
    )


def _make_type0_pdf(path: Path) -> None:
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(300, 200))
    font = _font(pdf, TO_UNICODE)
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page.Contents = pdf.make_stream(
        b"BT /F1 1 Tf 100 Tz 14 0 0 14 100 50 Tm <001300100015> Tj ET"
    )
    pdf.save(path)


def test_type0_replacement_reuses_codes_and_preserves_text_center(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    _make_type0_pdf(source)

    recognition = PageRecognition(
        page_number=1,
        source="0-2",
        text_rect=NormalizedRect(0.3, 0.7, 0.5, 0.8),
    )
    report = replace_exact_type0(source, output, [recognition], {"0-2": "001"})

    with pikepdf.open(output) as pdf:
        instructions = list(pikepdf.parse_content_stream(pdf.pages[0]))
        matrix = next(item for item in instructions if str(item.operator) == "Tm")
        text = next(item for item in instructions if str(item.operator) == "Tj")

    assert report.fixed_replacements == 1
    assert report.global_replacements == 0
    assert bytes(text.operands[0]) == bytes.fromhex("001300130014")
    assert abs(float(matrix.operands[4]) - 101.05) < 0.001


def test_type0_replacement_shares_missing_character_maps_across_subsets(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    second_cmap = TO_UNICODE.replace(b"<0014> <0031>\n", b"").replace(
        b"5 beginbfchar", b"4 beginbfchar"
    )
    pdf = pikepdf.Pdf.new()
    first = pdf.add_blank_page(page_size=(300, 200))
    second = pdf.add_blank_page(page_size=(300, 200))
    first.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=_font(pdf, TO_UNICODE, "/ABCDEF+Test"))
    )
    second.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=_font(pdf, second_cmap, "/GHIJKL+Test"))
    )
    first.Contents = pdf.make_stream(
        b"BT /F1 1 Tf 14 0 0 14 100 50 Tm <001300100015> Tj ET"
    )
    second.Contents = pdf.make_stream(
        b"BT /F1 1 Tf 14 0 0 14 100 50 Tm <002400100015> Tj ET"
    )
    pdf.save(source)

    recognitions = [
        PageRecognition(1, "0-2", NormalizedRect(0.3, 0.7, 0.5, 0.8)),
        PageRecognition(2, "A-2", NormalizedRect(0.3, 0.7, 0.5, 0.8)),
    ]
    replace_exact_type0(
        source,
        output,
        recognitions,
        {"0-2": "000", "A-2": "001"},
    )

    with pikepdf.open(output) as result:
        second_page = result.pages[1]
        instructions = list(pikepdf.parse_content_stream(second_page))
        text = next(item for item in instructions if str(item.operator) == "Tj")
        cmap = bytes(second_page.Resources.Font.F1.ToUnicode.read_bytes())

    assert bytes(text.operands[0]) == bytes.fromhex("001300130014")
    assert b"<0014> <0031>" in cmap
