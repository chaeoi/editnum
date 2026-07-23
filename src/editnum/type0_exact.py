from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import pikepdf

from .models import PageRecognition, matching_key
from .type3_exact import ExactResult


class ExactType0NotSupported(RuntimeError):
    pass


@dataclass(frozen=True)
class _CidFont:
    pdf_font: object
    code_to_text: dict[bytes, str]
    text_to_code: dict[str, bytes]
    local_texts: frozenset[str]
    widths: dict[int, float]
    default_width: float
    code_size: int

    def decode(self, value: object) -> str:
        data = bytes(value)
        if len(data) % self.code_size:
            raise ExactType0NotSupported(
                "Type 0 text uses an unsupported variable-width code"
            )
        return "".join(
            self.code_to_text.get(data[index : index + self.code_size], "\0")
            for index in range(0, len(data), self.code_size)
        )

    def encode(self, text: str) -> bytes:
        try:
            codes = [self.text_to_code[char] for char in text]
        except KeyError as exc:
            raise ExactType0NotSupported(
                f"Embedded Type 0 font is missing target character: {exc.args[0]}"
            ) from exc
        for code in codes:
            if not _cid_available(self.pdf_font, int.from_bytes(code, "big")):
                raise ExactType0NotSupported(
                    f"Embedded Type 0 font is missing target glyph CID {int.from_bytes(code, 'big')}"
                )
        return b"".join(codes)

    def extend_to_unicode(self, text: str) -> None:
        missing = sorted(set(text) - set(self.local_texts))
        if not missing:
            return
        content = bytes(self.pdf_font.ToUnicode.read_bytes()).decode("latin1")
        entries = []
        for char in missing:
            code = self.text_to_code[char].hex().upper()
            target = char.encode("utf-16-be").hex().upper()
            entries.append(f"<{code}> <{target}>")
        addition = (
            f"{len(entries)} beginbfchar\n" + "\n".join(entries) + "\nendbfchar\n"
        )
        position = content.rfind("endcmap")
        if position < 0:
            raise ExactType0NotSupported("Invalid ToUnicode character map")
        updated = content[:position] + addition + content[position:]
        self.pdf_font.ToUnicode.write(updated.encode("latin1"))

    def advance(
        self, data: bytes, font_size: float, char_space: float, word_space: float
    ) -> float:
        codes = [
            data[index : index + self.code_size]
            for index in range(0, len(data), self.code_size)
        ]
        glyph_width = sum(
            self.widths.get(int.from_bytes(code, "big"), self.default_width)
            for code in codes
        )
        spaces = sum(self.code_to_text.get(code) == " " for code in codes)
        return (
            glyph_width * font_size / 1000
            + len(codes) * char_space
            + spaces * word_space
        )


@dataclass
class _Match:
    stream: object
    instructions: list
    text_index: int
    matrix_index: int
    font: _CidFont
    horizontal_scale: float
    font_size: float
    char_space: float
    word_space: float


def _unicode_from_hex(value: str) -> str:
    try:
        return bytes.fromhex(value).decode("utf-16-be")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ExactType0NotSupported("Invalid ToUnicode character map") from exc


def _to_unicode_map(font: object) -> dict[bytes, str]:
    try:
        content = bytes(font.ToUnicode.read_bytes()).decode("latin1")
    except Exception as exc:
        raise ExactType0NotSupported(
            "Type 0 font has no readable ToUnicode map"
        ) from exc

    result: dict[bytes, str] = {}
    for section in re.findall(r"beginbfchar(.*?)endbfchar", content, flags=re.S):
        for source, target in re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", section
        ):
            result[bytes.fromhex(source)] = _unicode_from_hex(target)

    for section in re.findall(r"beginbfrange(.*?)endbfrange", content, flags=re.S):
        direct_ranges = re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>",
            section,
        )
        for first, last, target in direct_ranges:
            first_code = int(first, 16)
            last_code = int(last, 16)
            first_target = int(target, 16)
            code_size = len(first) // 2
            target_size = len(target) // 2
            for offset, code in enumerate(range(first_code, last_code + 1)):
                source_bytes = code.to_bytes(code_size, "big")
                target_bytes = (first_target + offset).to_bytes(target_size, "big")
                result[source_bytes] = _unicode_from_hex(target_bytes.hex())

        array_ranges = re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]",
            section,
            flags=re.S,
        )
        for first, last, targets in array_ranges:
            values = re.findall(r"<([0-9A-Fa-f]+)>", targets)
            first_code = int(first, 16)
            last_code = int(last, 16)
            code_size = len(first) // 2
            if len(values) != last_code - first_code + 1:
                raise ExactType0NotSupported("Invalid ToUnicode range")
            for code, target in zip(range(first_code, last_code + 1), values):
                result[code.to_bytes(code_size, "big")] = _unicode_from_hex(target)

    if not result:
        raise ExactType0NotSupported("Type 0 font has an empty ToUnicode map")
    return result


def _font_widths(font: object) -> tuple[dict[int, float], float]:
    try:
        descendant = font.DescendantFonts[0]
        values = list(descendant.get("/W", []))
        default_width = float(descendant.get("/DW", 1000))
    except Exception as exc:
        raise ExactType0NotSupported("Type 0 font has invalid CID widths") from exc

    widths: dict[int, float] = {}
    index = 0
    while index < len(values):
        first = int(values[index])
        following = values[index + 1]
        if isinstance(following, pikepdf.Array):
            for offset, width in enumerate(following):
                widths[first + offset] = float(width)
            index += 2
            continue
        last = int(following)
        width = float(values[index + 2])
        for code in range(first, last + 1):
            widths[code] = width
        index += 3
    return widths, default_width


def _cid_available(font: object, cid: int) -> bool:
    try:
        data = bytes(font.DescendantFonts[0].FontDescriptor.CIDSet.read_bytes())
    except Exception:
        return True
    index = cid // 8
    return index < len(data) and bool(data[index] & (1 << (7 - cid % 8)))


def _font_family(font: object) -> str:
    name = str(font.get("/BaseFont", "")).lstrip("/")
    return re.sub(r"^[A-Z]{6}\+", "", name)


def _cid_font(
    font: object,
    shared: tuple[dict[bytes, str], dict[str, bytes], dict[int, float]] | None = None,
) -> _CidFont:
    if str(font.get("/Subtype", "")) != "/Type0":
        raise ExactType0NotSupported("Font is not Type 0")
    if str(font.get("/Encoding", "")) != "/Identity-H":
        raise ExactType0NotSupported("Type 0 font does not use Identity-H encoding")
    code_to_text = _to_unicode_map(font)
    code_sizes = {len(code) for code in code_to_text}
    if len(code_sizes) != 1:
        raise ExactType0NotSupported("Type 0 font uses mixed-width character codes")
    local_text_to_code = {
        text: code for code, text in code_to_text.items() if len(text) == 1
    }
    widths, default_width = _font_widths(font)
    local_texts = frozenset(local_text_to_code)
    text_to_code = dict(local_text_to_code)
    if shared is not None:
        shared_code_to_text, shared_text_to_code, shared_widths = shared
        code_to_text.update(shared_code_to_text)
        text_to_code.update(shared_text_to_code)
        widths.update(shared_widths)
    return _CidFont(
        pdf_font=font,
        code_to_text=code_to_text,
        text_to_code=text_to_code,
        local_texts=local_texts,
        widths=widths,
        default_width=default_width,
        code_size=next(iter(code_sizes)),
    )


def _instruction(operator: str, operands: list[object]) -> object:
    return pikepdf.ContentStreamInstruction(operands, pikepdf.Operator(operator))


def _page_matches(
    page: object,
    source_key: str,
    shared_fonts: dict[str, tuple[dict[bytes, str], dict[str, bytes], dict[int, float]]]
    | None = None,
) -> list[_Match]:
    try:
        fonts = {
            str(name): _cid_font(font, (shared_fonts or {}).get(_font_family(font)))
            for name, font in page.Resources.Font.items()
        }
    except ExactType0NotSupported:
        raise
    except Exception as exc:
        raise ExactType0NotSupported(
            "Page has no supported Type 0 font resources"
        ) from exc

    matches: list[_Match] = []
    contents = (
        list(page.Contents)
        if isinstance(page.Contents, pikepdf.Array)
        else [page.Contents]
    )
    current_font: _CidFont | None = None
    font_size = 1.0
    horizontal_scale = 100.0
    char_space = 0.0
    word_space = 0.0
    for stream in contents:
        instructions = list(pikepdf.parse_content_stream(stream))
        matrix_index: int | None = None
        for index, item in enumerate(instructions):
            operator = str(item.operator)
            if operator == "Tf":
                current_font = fonts.get(str(item.operands[0]))
                font_size = float(item.operands[1])
            elif operator == "Tz":
                horizontal_scale = float(item.operands[0])
            elif operator == "Tc":
                char_space = float(item.operands[0])
            elif operator == "Tw":
                word_space = float(item.operands[0])
            elif operator == "Tm":
                matrix_index = index
            elif (
                operator == "Tj"
                and current_font is not None
                and matrix_index is not None
            ):
                decoded = current_font.decode(item.operands[0])
                if matching_key(decoded) == source_key:
                    matches.append(
                        _Match(
                            stream=stream,
                            instructions=instructions,
                            text_index=index,
                            matrix_index=matrix_index,
                            font=current_font,
                            horizontal_scale=horizontal_scale,
                            font_size=font_size,
                            char_space=char_space,
                            word_space=word_space,
                        )
                    )
    return matches


def _shared_font_maps(
    pages: object,
) -> dict[str, tuple[dict[bytes, str], dict[str, bytes], dict[int, float]]]:
    combined: dict[
        str, tuple[dict[bytes, str], dict[str, bytes], dict[int, float]]
    ] = {}
    for page in pages:
        try:
            fonts = page.Resources.Font.items()
        except Exception:
            continue
        for _name, font in fonts:
            try:
                info = _cid_font(font)
            except ExactType0NotSupported:
                continue
            family = _font_family(font)
            code_to_text, text_to_code, widths = combined.setdefault(
                family, ({}, {}, {})
            )
            for code, text in info.code_to_text.items():
                previous = code_to_text.setdefault(code, text)
                if previous != text:
                    raise ExactType0NotSupported(
                        f"Type 0 font family {family} has conflicting CID mappings"
                    )
            for text, code in info.text_to_code.items():
                previous = text_to_code.setdefault(text, code)
                if previous != code:
                    raise ExactType0NotSupported(
                        f"Type 0 font family {family} has conflicting character mappings"
                    )
            widths.update(info.widths)
    return combined


def replace_exact_type0(
    source_path: Path,
    output_path: Path,
    recognitions: list[PageRecognition],
    mapping_by_key: dict[str, str],
) -> ExactResult:
    pdf = pikepdf.open(source_path)
    try:
        if len(pdf.pages) != len(recognitions):
            raise ExactType0NotSupported("Page count changed after recognition")

        shared_fonts = _shared_font_maps(pdf.pages)
        modified: dict[tuple[int, int], tuple[object, list]] = {}
        for page, recognition in zip(pdf.pages, recognitions):
            source_key = matching_key(recognition.source)
            matches = _page_matches(page, source_key, shared_fonts)
            if len(matches) != 1:
                raise ExactType0NotSupported(
                    f"Page {recognition.page_number} has {len(matches)} exact Type 0 title matches"
                )
            match = matches[0]
            target = mapping_by_key[source_key]
            old_data = bytes(match.instructions[match.text_index].operands[0])
            new_data = match.font.encode(target)
            match.font.extend_to_unicode(target)
            old_advance = match.font.advance(
                old_data, match.font_size, match.char_space, match.word_space
            )
            new_advance = match.font.advance(
                new_data, match.font_size, match.char_space, match.word_space
            )

            matrix = [
                float(value)
                for value in match.instructions[match.matrix_index].operands
            ]
            if len(matrix) != 6:
                raise ExactType0NotSupported("Invalid Type 0 text matrix")
            centered_shift = (old_advance - new_advance) * match.horizontal_scale / 200
            matrix[4] += centered_shift * matrix[0]
            matrix[5] += centered_shift * matrix[1]
            match.instructions[match.matrix_index] = _instruction("Tm", matrix)
            match.instructions[match.text_index] = _instruction(
                "Tj", [pikepdf.String(new_data)]
            )
            stream_key = tuple(match.stream.objgen)
            if stream_key in modified:
                raise ExactType0NotSupported(
                    "Multiple pages share the same Type 0 content stream"
                )
            modified[stream_key] = (match.stream, match.instructions)

        for stream, instructions in modified.values():
            stream.write(pikepdf.unparse_content_stream(instructions))
        pdf.save(output_path)
    finally:
        pdf.close()

    return ExactResult(
        fixed_replacements=len(recognitions),
        global_replacements=0,
        log_lines=(
            "字体策略：精确复用嵌入的 CID TrueType 字形",
            "原字体、字号、基线和文字中心均已保留",
        ),
    )
