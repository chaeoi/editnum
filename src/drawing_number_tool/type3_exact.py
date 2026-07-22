from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
from pathlib import Path

import pikepdf

from .models import PageRecognition, matching_key


NEW_GLYPH_CODES = {
    "0": 200,
    "1": 201,
    "2": 202,
    "3": 203,
    "4": 204,
    "5": 205,
    "6": 206,
    "7": 207,
    "8": 208,
    "9": 209,
    "B": 210,
    "J": 211,
    "K": 212,
    "L": 213,
    "Q": 214,
    "R": 215,
    "-": 216,
}

ADVANCE = {
    "0": 0.351,
    "1": 0.351,
    "2": 0.351,
    "3": 0.351,
    "4": 0.351,
    "5": 0.351,
    "6": 0.350,
    "7": 0.350,
    "8": 0.350,
    "9": 0.350,
    "B": 0.350,
    "J": 0.289,
    "K": 0.350,
    "L": 0.289,
    "Q": 0.350,
    "R": 0.350,
    "-": 0.493,
}


class ExactType3NotSupported(RuntimeError):
    pass


@dataclass(frozen=True)
class ExactResult:
    fixed_replacements: int
    global_replacements: int
    log_lines: tuple[str, ...]


@dataclass
class _Block:
    stream: object
    instructions: list
    start: int
    end: int
    font: object
    source: str
    target: str
    fixed: bool


def _instruction(operator, *operands):
    return pikepdf.ContentStreamInstruction(list(operands), pikepdf.Operator(operator))


def _font_code_map(font) -> dict[int, str]:
    result: dict[int, str] = {}
    code = None
    for item in font.Encoding.Differences:
        if isinstance(item, int):
            code = int(item)
        else:
            if code is None:
                raise ExactType3NotSupported("Invalid Type 3 encoding")
            result[code] = str(item)
            code += 1
    return result


def _is_title_matrix(values) -> bool:
    return (
        len(values) == 6
        and abs(float(values[4]) - 531.2477) < 0.02
        and 760.7 < float(values[5]) < 761.5
        and 9.7 < float(values[1]) < 10.0
    )


def _contents(page):
    return list(page.Contents) if isinstance(page.Contents, pikepdf.Array) else [page.Contents]


def _shown_codes(instructions) -> list[int]:
    result: list[int] = []
    for item in instructions:
        if str(item.operator) == "Tj":
            result.extend(bytes(item.operands[0]))
    return result


def _find_title(page):
    for stream in _contents(page):
        instructions = list(pikepdf.parse_content_stream(stream))
        for start, item in enumerate(instructions):
            if str(item.operator) != "Tm" or not _is_title_matrix(item.operands):
                continue
            end = start + 1
            while end < len(instructions) and str(instructions[end].operator) not in ("Tm", "ET"):
                end += 1
            return stream, instructions, start, end
    raise ExactType3NotSupported("The precise Type 3 title block was not found")


def _glyph_digest(font, code_map: dict[int, str], code: int) -> str:
    try:
        glyph = font.CharProcs[code_map[code]]
    except (KeyError, AttributeError) as exc:
        raise ExactType3NotSupported(f"Missing Type 3 glyph code {code}") from exc
    return hashlib.sha256(glyph.read_bytes()).hexdigest()


def _custom_stroke_glyph(paths, curves=None) -> bytes:
    lines = [
        "1 0 -10 -10 10 10 d1",
        "q",
        "0 G",
        ".9 w",
        "1 J 1 j",
        ".0146484 0 0 -.0307617 .175781 .328125 cm",
    ]
    if curves:
        lines.extend(curves)
    for path in paths:
        for index, (x, y) in enumerate(path):
            lines.append(f"{x:g} {y:g} {'m' if index == 0 else 'l'}")
        lines.append("S")
    lines.append("Q")
    return "\n".join(lines).encode("ascii")


def _make_missing_glyphs(pdf) -> dict[str, object]:
    q_curves = [
        "0 -12 m",
        "4.41828 -12 8 -7.29899 8 -1.5 c",
        "8 4.29899 4.41828 9 0 9 c",
        "-4.41828 9 -8 4.29899 -8 -1.5 c",
        "-8 -7.29899 -4.41828 -12 0 -12 c",
        "S",
    ]
    return {
        "Q": pikepdf.Stream(pdf, _custom_stroke_glyph([[(1, 5), (7, 11)]], q_curves)),
        "K": pikepdf.Stream(
            pdf,
            _custom_stroke_glyph(
                [[(-7, -12), (-7, 9)], [(7, -12), (-7, 2)], [(-2, -3), (7, 9)]]
            ),
        ),
        "J": pikepdf.Stream(
            pdf,
            _custom_stroke_glyph(
                [[(4, -12), (4, 4), (3, 7), (2, 8), (0, 9), (-2, 9), (-4, 8), (-5, 7), (-6, 4), (-6, 2)]]
            ),
        ),
    }


def _add_replacement_glyphs(font, glyphs: dict[str, object]) -> None:
    if int(font.LastChar) >= min(NEW_GLYPH_CODES.values()):
        raise ExactType3NotSupported("Replacement glyph codes collide with this font")
    differences = font.Encoding.Differences
    differences.append(min(NEW_GLYPH_CODES.values()))
    for char, code in sorted(NEW_GLYPH_CODES.items(), key=lambda item: item[1]):
        name = pikepdf.Name(f"/DrawingNumber{code}")
        differences.append(name)
        font.CharProcs[name] = glyphs[char]

    first_char = int(font.FirstChar)
    new_last_char = max(NEW_GLYPH_CODES.values())
    while len(font.Widths) < new_last_char - first_char + 1:
        font.Widths.append(0)
    for code in NEW_GLYPH_CODES.values():
        font.Widths[code - first_char] = 1
    font.LastChar = new_last_char


def replace_exact_type3(
    source_path: Path,
    output_path: Path,
    recognitions: list[PageRecognition],
    mapping_by_key: dict[str, str],
    global_search: bool = True,
) -> ExactResult:
    pdf = pikepdf.open(source_path)
    if len(pdf.pages) != len(recognitions):
        raise ExactType3NotSupported("Page count changed after recognition")
    if any(set(target) - set(NEW_GLYPH_CODES) for target in mapping_by_key.values()):
        raise ExactType3NotSupported("The target contains glyphs not supported by the exact CAD font editor")

    page_titles = {item.page_number: item.source for item in recognitions}
    glyph_hash_to_char: dict[str, str] = {}
    canonical_glyphs: dict[str, object] = {}
    fixed_refs = []

    for page_number, page in enumerate(pdf.pages, 1):
        fonts = [font for _, font in page.Resources.Font.items()]
        if len(fonts) != 1 or str(fonts[0].Subtype) != "/Type3":
            raise ExactType3NotSupported("This PDF is not the supported single-font CAD Type 3 layout")
        font = fonts[0]
        stream, instructions, start, end = _find_title(page)
        codes = _shown_codes(instructions[start:end])
        source = page_titles[page_number]
        if len(codes) != len(source):
            raise ExactType3NotSupported(
                f"Page {page_number}: OCR length does not match the precise text block"
            )
        code_map = _font_code_map(font)
        for code, char in zip(codes, source):
            digest = _glyph_digest(font, code_map, code)
            old = glyph_hash_to_char.setdefault(digest, char)
            if matching_key(old) != matching_key(char):
                raise ExactType3NotSupported("OCR conflicts with the embedded glyph mapping")
            if char in NEW_GLYPH_CODES and char not in {"Q", "K", "J"}:
                canonical_glyphs.setdefault(char, font.CharProcs[code_map[code]])
        fixed_refs.append((page_number, stream, instructions, start, end, font))

    required = set().union(*(set(value) for value in mapping_by_key.values()))
    missing = (required - {"Q", "K", "J"}) - set(canonical_glyphs)
    if missing:
        raise ExactType3NotSupported(f"Source font is missing target glyphs: {sorted(missing)}")
    canonical_glyphs.update(_make_missing_glyphs(pdf))

    blocks_by_stream: dict[tuple[int, int], list[_Block]] = defaultdict(list)
    stream_objects: dict[tuple[int, int], object] = {}
    instruction_lists: dict[tuple[int, int], list] = {}

    for page_number, page in enumerate(pdf.pages, 1):
        font = next(font for _, font in page.Resources.Font.items())
        code_map = _font_code_map(font)
        for stream in _contents(page):
            instructions = list(pikepdf.parse_content_stream(stream))
            stream_key = tuple(stream.objgen)
            stream_objects[stream_key] = stream
            instruction_lists[stream_key] = instructions
            for start, item in enumerate(instructions):
                if str(item.operator) != "Tm":
                    continue
                end = start + 1
                while end < len(instructions) and str(instructions[end].operator) not in ("Tm", "ET"):
                    end += 1
                codes = _shown_codes(instructions[start:end])
                if not codes:
                    continue
                try:
                    decoded = "".join(glyph_hash_to_char[_glyph_digest(font, code_map, code)] for code in codes)
                except (KeyError, ExactType3NotSupported):
                    continue
                key = matching_key(decoded)
                if key not in mapping_by_key:
                    continue
                fixed = _is_title_matrix(item.operands)
                if not fixed and not global_search:
                    continue
                blocks_by_stream[stream_key].append(
                    _Block(
                        stream=stream,
                        instructions=instructions,
                        start=start,
                        end=end,
                        font=font,
                        source=decoded,
                        target=mapping_by_key[key],
                        fixed=fixed,
                    )
                )

    fixed_count = sum(block.fixed for blocks in blocks_by_stream.values() for block in blocks)
    if fixed_count != len(pdf.pages):
        raise ExactType3NotSupported("Not every page has exactly one precise title occurrence")

    prepared_fonts: set[tuple[int, int]] = set()
    for stream_key, blocks in blocks_by_stream.items():
        instructions = instruction_lists[stream_key]
        for block in sorted(blocks, key=lambda item: item.start, reverse=True):
            font_key = tuple(block.font.objgen)
            if font_key not in prepared_fonts:
                _add_replacement_glyphs(block.font, canonical_glyphs)
                prepared_fonts.add(font_key)
            old_advance = sum(
                float(item.operands[0])
                for item in instructions[block.start : block.end]
                if str(item.operator) == "TD"
            )
            matrix = [float(value) for value in instructions[block.start].operands]
            new_advance = sum(ADVANCE[char] for char in block.target[:-1])
            matrix[5] -= (new_advance - old_advance) * matrix[1] / 2
            if block.fixed:
                matrix[5] -= 2.1
            replacement = [_instruction("Tm", *matrix)]
            for index, char in enumerate(block.target):
                replacement.append(
                    _instruction("Tj", pikepdf.String(bytes([NEW_GLYPH_CODES[char]])))
                )
                if index != len(block.target) - 1:
                    replacement.append(_instruction("TD", ADVANCE[char], 0))
            instructions[block.start : block.end] = replacement
        stream_objects[stream_key].write(pikepdf.unparse_content_stream(instructions))

    pdf.save(output_path)
    global_count = sum(not block.fixed for blocks in blocks_by_stream.values() for block in blocks)
    return ExactResult(
        fixed_replacements=fixed_count,
        global_replacements=global_count,
        log_lines=(
            "Font strategy: exact embedded Type 3 CAD glyph reuse",
            "Font size, text matrix and source character spacing were preserved",
            f"Content-stream global matches outside title blocks: {global_count}",
        ),
    )
