#!/usr/bin/env python3
import csv
import hashlib
from collections import defaultdict
from pathlib import Path

import pikepdf


SOURCE = Path("")
OUTPUT = Path("")
MAPPING_CSV = Path("")

OLD_TITLES = [

]

NEW_GLYPH_CODES = {

}

# Original title-block advances, in text-space units. The PDF explicitly
# positions every glyph, so these values preserve the source letter spacing.
ADVANCE = {

}


def instruction(operator, *operands):
    return pikepdf.ContentStreamInstruction(list(operands), pikepdf.Operator(operator))


def font_code_map(font):
    result = {}
    code = None
    for item in font.Encoding.Differences:
        if isinstance(item, int):
            code = item
        else:
            if code is None:
                raise ValueError("Invalid Type 3 font encoding")
            result[code] = str(item)
            code += 1
    return result


def is_title_matrix(values):
    return (
        len(values) == 6
        and abs(float(values[4]) - 531.2477) < 0.01
        and 760.8 < float(values[5]) < 761.4
        and 9.7 < float(values[1]) < 10.0
    )


def find_title_block(page):
    contents = list(page.Contents) if isinstance(page.Contents, pikepdf.Array) else [page.Contents]
    for stream in contents:
        instructions = list(pikepdf.parse_content_stream(stream))
        for start, item in enumerate(instructions):
            if str(item.operator) != "Tm" or not is_title_matrix(item.operands):
                continue
            end = start + 1
            while end < len(instructions) and str(instructions[end].operator) not in ("Tm", "ET"):
                end += 1
            return stream, instructions, start, end
    raise ValueError("Title-block drawing number was not found")


def shown_codes(instructions):
    result = []
    for item in instructions:
        if str(item.operator) == "Tj":
            result.extend(bytes(item.operands[0]))
    return result


def custom_stroke_glyph(paths, curves=None):
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


def make_missing_glyphs(pdf):
    # The source is a condensed single-stroke engineering font. These three
    # letters are absent from its subsets, so add matching Type 3 outlines at
    # the source glyph scale and stroke weight.
    q_curves = [
        "0 -12 m",
        "4.41828 -12 8 -7.29899 8 -1.5 c",
        "8 4.29899 4.41828 9 0 9 c",
        "-4.41828 9 -8 4.29899 -8 -1.5 c",
        "-8 -7.29899 -4.41828 -12 0 -12 c",
        "S",
    ]
    return {
        "Q": pikepdf.Stream(pdf, custom_stroke_glyph([[(1, 5), (7, 11)]], q_curves)),
        "K": pikepdf.Stream(
            pdf,
            custom_stroke_glyph(
                [
                    [(-7, -12), (-7, 9)],
                    [(7, -12), (-7, 2)],
                    [(-2, -3), (7, 9)],
                ]
            ),
        ),
        "J": pikepdf.Stream(
            pdf,
            custom_stroke_glyph(
                [[(4, -12), (4, 4), (3, 7), (2, 8), (0, 9), (-2, 9), (-4, 8), (-5, 7), (-6, 4), (-6, 2)]]
            ),
        ),
    }


def add_replacement_glyphs(font, glyphs):
    if int(font.LastChar) >= min(NEW_GLYPH_CODES.values()):
        raise ValueError("Replacement glyph codes collide with the source font")

    differences = font.Encoding.Differences
    differences.append(min(NEW_GLYPH_CODES.values()))
    for char, code in sorted(NEW_GLYPH_CODES.items(), key=lambda item: item[1]):
        name = pikepdf.Name(f"/DrawingNumber{code}")
        differences.append(name)
        font.CharProcs[name] = glyphs[char]

    first_char = int(font.FirstChar)
    new_last_char = max(NEW_GLYPH_CODES.values())
    required_widths = new_last_char - first_char + 1
    while len(font.Widths) < required_widths:
        font.Widths.append(0)
    for code in NEW_GLYPH_CODES.values():
        font.Widths[code - first_char] = 1
    font.LastChar = new_last_char


def build_mapping():
    mapping = {}
    pages = defaultdict(list)
    for page_number, old_title in enumerate(OLD_TITLES, 1):
        pages[old_title].append(page_number)
        if old_title not in mapping:
            sequence = len(mapping) + 1
            mapping[old_title] = f"00QK0188RJB-LJ-{sequence:03d}-0-260715"
    return mapping, pages


def main():
    mapping, pages_by_title = build_mapping()
    pdf = pikepdf.open(SOURCE)
    if len(pdf.pages) != len(OLD_TITLES):
        raise ValueError(f"Expected {len(OLD_TITLES)} pages, found {len(pdf.pages)}")

    page_data = []
    canonical_glyphs = {}
    glyph_hash_to_char = {}

    for page_number, (page, old_title) in enumerate(zip(pdf.pages, OLD_TITLES), 1):
        font = next(font for _, font in page.Resources.Font.items())
        code_map = font_code_map(font)
        stream, instructions, start, end = find_title_block(page)
        codes = shown_codes(instructions[start:end])
        if len(codes) != len(old_title):
            raise ValueError(f"Page {page_number}: unexpected drawing-number length")

        for code, char in zip(codes, old_title):
            glyph = font.CharProcs[code_map[code]]
            digest = hashlib.sha256(glyph.read_bytes()).hexdigest()
            previous = glyph_hash_to_char.setdefault(digest, char)
            if previous != char:
                raise ValueError(f"Page {page_number}: inconsistent source glyph mapping")
            if char in NEW_GLYPH_CODES and char not in ("Q", "K", "J"):
                canonical_glyphs.setdefault(char, glyph)

        old_advance = sum(
            float(item.operands[0])
            for item in instructions[start:end]
            if str(item.operator) == "TD"
        )
        page_data.append((font, stream, instructions, start, end, old_advance))

    missing_source_glyphs = (set(NEW_GLYPH_CODES) - {"Q", "K", "J"}) - set(canonical_glyphs)
    if missing_source_glyphs:
        raise ValueError(f"Source font is missing required glyphs: {sorted(missing_source_glyphs)}")
    canonical_glyphs.update(make_missing_glyphs(pdf))

    for page_number, (old_title, data) in enumerate(zip(OLD_TITLES, page_data), 1):
        font, stream, instructions, start, end, old_advance = data
        new_title = mapping[old_title]
        add_replacement_glyphs(font, canonical_glyphs)

        matrix = [float(value) for value in instructions[start].operands]
        new_advance = sum(ADVANCE[char] for char in new_title[:-1])
        matrix[5] -= (new_advance - old_advance) * matrix[1] / 2
        matrix[5] -= 2.1

        replacement = [instruction("Tm", *matrix)]
        for index, char in enumerate(new_title):
            replacement.append(instruction("Tj", pikepdf.String(bytes([NEW_GLYPH_CODES[char]]))))
            if index != len(new_title) - 1:
                replacement.append(instruction("TD", ADVANCE[char], 0))

        instructions[start:end] = replacement
        stream.write(pikepdf.unparse_content_stream(instructions))

    pdf.save(OUTPUT)

    with MAPPING_CSV.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["原图号", "新图号", "页码"])
        for old_title, new_title in mapping.items():
            writer.writerow([old_title, new_title, ",".join(map(str, pages_by_title[old_title]))])

    print(f"Wrote {OUTPUT}")
    print(f"Wrote {MAPPING_CSV}")
    print(f"Pages: {len(OLD_TITLES)}, unique drawing numbers: {len(mapping)}")


if __name__ == "__main__":
    main()
