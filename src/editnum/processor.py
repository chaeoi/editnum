from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import os
from pathlib import Path
import threading
from typing import Callable

import pymupdf
from PIL import Image

from .models import (
    NormalizedRect,
    PageRecognition,
    ProcessOptions,
    ProcessReport,
    build_mapping,
    matching_key,
)
from .ocr import OcrError, RapidOcrEngine, find_mapping_matches, primary_text
from .type0_exact import ExactType0NotSupported, replace_exact_type0
from .type3_exact import ExactType3NotSupported, replace_exact_type3
from .version import __version__


ProgressCallback = Callable[[int, int, str], None]


def _noop_progress(current: int, total: int, message: str) -> None:
    del current, total, message


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Operation cancelled")


def render_page(page: pymupdf.Page, dpi: int) -> Image.Image:
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False)
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def recognize_fixed_regions(
    source_path: Path,
    selection: NormalizedRect,
    options: ProcessOptions,
    ocr: RapidOcrEngine | None = None,
    progress: ProgressCallback = _noop_progress,
    cancel_event: threading.Event | None = None,
) -> list[PageRecognition]:
    options.validate()
    engine = ocr or RapidOcrEngine()
    document = pymupdf.open(source_path)
    recognitions: list[PageRecognition] = []
    total = len(document)
    try:
        for index, page in enumerate(document):
            _check_cancel(cancel_event)
            progress(index, total, f"正在识别第 {index + 1}/{total} 页")
            image = render_page(page, options.fixed_dpi)
            crop_box = _pixel_box(selection, image.width, image.height)
            spans = engine.recognize(image.crop(crop_box))
            try:
                text, _score, local_box = primary_text(spans)
            except OcrError as exc:
                raise OcrError(f"第 {index + 1} 页选定区域未识别到文本") from exc
            global_box = (
                crop_box[0] + local_box[0],
                crop_box[1] + local_box[1],
                crop_box[0] + local_box[2],
                crop_box[1] + local_box[3],
            )
            text_rect = NormalizedRect(
                max(0.0, global_box[0] / image.width),
                max(0.0, global_box[1] / image.height),
                min(1.0, global_box[2] / image.width),
                min(1.0, global_box[3] / image.height),
            )
            recognitions.append(
                    PageRecognition(
                        page_number=index + 1,
                        source=text,
                        text_rect=text_rect,
                        raw_text=text,
                )
            )
        progress(total, total, "各页选区识别完成")
        return recognitions
    finally:
        document.close()


def _pixel_box(rect: NormalizedRect, width: int, height: int) -> tuple[int, int, int, int]:
    return (
        max(0, int(rect.x0 * width)),
        max(0, int(rect.y0 * height)),
        min(width, max(1, math.ceil(rect.x1 * width))),
        min(height, max(1, math.ceil(rect.y1 * height))),
    )


@dataclass
class _Hit:
    source_key: str
    target: str
    clear_rect: pymupdf.Rect
    placement_rect: pymupdf.Rect
    kind: str
    display_height: float
    reflow_line: bool = False


def process_document(
    source_path: Path,
    output_path: Path,
    selection: NormalizedRect,
    recognitions: list[PageRecognition],
    options: ProcessOptions,
    ocr: RapidOcrEngine | None = None,
    progress: ProgressCallback = _noop_progress,
    cancel_event: threading.Event | None = None,
) -> ProcessReport:
    options.validate()
    source_path = source_path.resolve()
    output_path = output_path.resolve()
    if source_path == output_path:
        raise ValueError("Output PDF must not overwrite the source PDF")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = output_path.with_suffix(".log")
    entries, mapping_by_key = build_mapping(recognitions, options)
    warnings: list[str] = []

    _check_cancel(cancel_event)
    exact_result = None
    exact_failure = ""
    progress(0, 1, "正在检查嵌入字体精确替换")
    if options.replacement_font_path:
        exact_failure = "用户已选择 Windows 字体，改用统一视觉排版"
    else:
        try:
            exact_result = replace_exact_type3(
                source_path,
                output_path,
                recognitions,
                mapping_by_key,
                global_search=options.global_search,
            )
        except ExactType3NotSupported as type3_exc:
            try:
                exact_result = replace_exact_type0(
                    source_path,
                    output_path,
                    recognitions,
                    mapping_by_key,
                )
            except ExactType0NotSupported as type0_exc:
                exact_failure = f"Type 3：{type3_exc}；Type 0：{type0_exc}"

    if exact_result is not None:
        supplemental_count = 0
        supplemental_exact = True
        supplemental_lines: list[str] = []
        if options.global_search:
            supplemental_count, supplemental_exact = _replace_remaining_ocr_hits(
                output_path,
                mapping_by_key,
                options,
                ocr or RapidOcrEngine(),
                progress,
                cancel_event,
                warnings,
                supplemental_lines,
            )
        report = ProcessReport(
            output_path=str(output_path),
            log_path=str(log_path),
            page_count=len(recognitions),
            mappings=entries,
            fixed_replacements=exact_result.fixed_replacements,
            global_replacements=exact_result.global_replacements + supplemental_count,
            exact_font_preserved=supplemental_exact,
            warnings=warnings,
        )
        _write_log(
            log_path,
            source_path,
            selection,
            recognitions,
            options,
            report,
            list(exact_result.log_lines) + supplemental_lines,
        )
        progress(1, 1, "PDF 与日志已生成")
        return report

    visual_lines = [f"精确 Type 3 路径不可用：{exact_failure}"]
    visual_count, visual_fonts_exact = _replace_visually(
        source_path,
        output_path,
        selection,
        recognitions,
        mapping_by_key,
        options,
        ocr or RapidOcrEngine(),
        progress,
        cancel_event,
        warnings,
        visual_lines,
    )
    report = ProcessReport(
        output_path=str(output_path),
        log_path=str(log_path),
        page_count=len(recognitions),
        mappings=entries,
        fixed_replacements=len(recognitions),
        global_replacements=visual_count,
        exact_font_preserved=visual_fonts_exact,
        warnings=warnings,
    )
    _write_log(
        log_path,
        source_path,
        selection,
        recognitions,
        options,
        report,
        visual_lines,
    )
    return report


def _display_rect(page: pymupdf.Page, rect: NormalizedRect) -> pymupdf.Rect:
    return pymupdf.Rect(
        rect.x0 * page.rect.width,
        rect.y0 * page.rect.height,
        rect.x1 * page.rect.width,
        rect.y1 * page.rect.height,
    )


def _to_unrotated(page: pymupdf.Page, rect: pymupdf.Rect) -> pymupdf.Rect:
    transformed = rect * page.derotation_matrix
    return pymupdf.Rect(
        min(transformed.x0, transformed.x1),
        min(transformed.y0, transformed.y1),
        max(transformed.x0, transformed.x1),
        max(transformed.y0, transformed.y1),
    )


def _replace_visually(
    source_path: Path,
    output_path: Path,
    selection: NormalizedRect,
    recognitions: list[PageRecognition],
    mapping_by_key: dict[str, str],
    options: ProcessOptions,
    ocr: RapidOcrEngine,
    progress: ProgressCallback,
    cancel_event: threading.Event | None,
    warnings: list[str],
    log_lines: list[str],
) -> tuple[int, bool]:
    document = pymupdf.open(source_path)
    if len(document) != len(recognitions):
        document.close()
        raise ValueError("Page count changed after recognition")
    hits_by_page: list[list[_Hit]] = []
    global_count = 0
    total_steps = len(document) * (2 if options.global_search else 1)
    all_fonts_exact = True
    try:
        for index, page in enumerate(document):
            _check_cancel(cancel_event)
            recognition = recognitions[index]
            source_key = matching_key(recognition.source)
            fixed_clear_display = _display_rect(page, recognition.text_rect)
            hits = [
                _Hit(
                    source_key=source_key,
                    target=mapping_by_key[source_key],
                    clear_rect=_to_unrotated(page, fixed_clear_display),
                    placement_rect=_to_unrotated(page, fixed_clear_display),
                    kind="选区位置",
                    display_height=fixed_clear_display.height,
                )
            ]
            if options.global_search:
                progress(index, total_steps, f"正在搜索全文第 {index + 1}/{len(document)} 页")
                global_hits = _find_global_hits(
                    page,
                    mapping_by_key,
                    fixed_clear_display,
                    options.global_dpi,
                    ocr,
                )
                hits.extend(global_hits)
            deduplicated = _deduplicate_hits(hits)
            global_count += sum(hit.kind != "选区位置" for hit in deduplicated)
            hits_by_page.append(deduplicated)

        for index, (page, hits) in enumerate(zip(document, hits_by_page)):
            _check_cancel(cancel_event)
            progress(
                len(document) + index if options.global_search else index,
                total_steps,
                f"正在写入第 {index + 1}/{len(document)} 页",
            )
            styles = [_detect_style(document, page, hit) for hit in hits]
            for hit in hits:
                clear_rect = pymupdf.Rect(hit.clear_rect)
                clear_rect.x0 -= 0.6
                clear_rect.y0 -= 0.6
                clear_rect.x1 += 0.6
                clear_rect.y1 += 0.6
                page.add_redact_annot(clear_rect, fill=(1, 1, 1), cross_out=False)
            page.apply_redactions(images=2, graphics=0, text=0)
            for hit, style in zip(hits, styles):
                used_size, strategy, font_exact = _insert_replacement(
                    document, page, hit, style, options
                )
                all_fonts_exact = all_fonts_exact and font_exact
                log_lines.append(
                    f"第 {index + 1} 页 {hit.kind}：{hit.target}，字体={strategy}，字号={used_size:.2f}"
                )
                if used_size + 0.05 < style.font_size:
                    warning = (
                        f"第 {index + 1} 页 {hit.kind}因空间不足，字号由 "
                        f"{style.font_size:.2f} 调整为 {used_size:.2f}"
                    )
                    warnings.append(warning)
            page.clean_contents()
        document.save(output_path, garbage=4, deflate=True)
        progress(total_steps, total_steps, "PDF 与日志已生成")
        return global_count, all_fonts_exact
    finally:
        document.close()


def _find_global_hits(
    page: pymupdf.Page,
    mapping_by_key: dict[str, str],
    fixed_display: pymupdf.Rect,
    dpi: int,
    ocr: RapidOcrEngine,
) -> list[_Hit]:
    hits: list[_Hit] = []
    text_lines = _page_text_lines(page)
    for source_key, target in mapping_by_key.items():
        for source_rect in page.search_for(source_key):
            display_rect = source_rect * page.rotation_matrix
            if _rect_overlap_ratio(display_rect, fixed_display) > 0.5:
                continue
            line = _containing_text_line(text_lines, source_rect, source_key)
            if line is not None:
                line_text, line_rect = line
                replacement = _replace_mapped_text(line_text, mapping_by_key)
                line_display = line_rect * page.rotation_matrix
                hits.append(
                    _Hit(
                        source_key=source_key,
                        target=replacement,
                        clear_rect=pymupdf.Rect(line_rect),
                        placement_rect=pymupdf.Rect(line_rect),
                        kind="文本层整行重排",
                        display_height=line_display.height,
                        reflow_line=True,
                    )
                )
                continue
            hits.append(
                _Hit(
                    source_key=source_key,
                    target=target,
                    clear_rect=pymupdf.Rect(source_rect),
                    placement_rect=pymupdf.Rect(source_rect),
                    kind="文本层全文命中",
                    display_height=display_rect.height,
                )
            )

    image = render_page(page, dpi)
    spans = ocr.recognize(image)
    for match in find_mapping_matches(spans, set(mapping_by_key)):
        hit, display_rect = _ocr_match_hit(page, image, match, mapping_by_key)
        if _rect_overlap_ratio(display_rect, fixed_display) > 0.5:
            continue
        hits.append(hit)
    return _deduplicate_hits(hits)


def _ocr_match_hit(
    page: pymupdf.Page,
    image: Image.Image,
    match,
    mapping_by_key: dict[str, str],
    exact_fallback: bool = False,
) -> tuple[_Hit, pymupdf.Rect]:
    standalone = matching_key(match.line_text) == match.key
    pixel_box = match.line_box if standalone else match.box
    display_rect = pymupdf.Rect(
        pixel_box[0] * page.rect.width / image.width,
        pixel_box[1] * page.rect.height / image.height,
        pixel_box[2] * page.rect.width / image.width,
        pixel_box[3] * page.rect.height / image.height,
    )
    page_rect = _to_unrotated(page, display_rect)
    if standalone:
        target = _replace_mapped_text(match.line_text, mapping_by_key)
        kind = "OCR 独立文字重排"
    else:
        target = mapping_by_key[match.key]
        kind = "OCR 局部替换"
    if exact_fallback:
        kind = f"精确替换后 {kind}"
    return (
        _Hit(
            source_key=match.key,
            target=target,
            clear_rect=page_rect,
            placement_rect=page_rect,
            kind=kind,
            display_height=display_rect.height,
            reflow_line=standalone,
        ),
        display_rect,
    )


def _page_text_lines(page: pymupdf.Page) -> list[tuple[str, pymupdf.Rect]]:
    result: list[tuple[str, pymupdf.Rect]] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(str(span.get("text", "")) for span in line.get("spans", []))
            if not text:
                continue
            result.append((text, pymupdf.Rect(line["bbox"])))
    return result


def _containing_text_line(
    lines: list[tuple[str, pymupdf.Rect]],
    source_rect: pymupdf.Rect,
    source_key: str,
) -> tuple[str, pymupdf.Rect] | None:
    candidates = [
        line
        for line in lines
        if source_key in matching_key(line[0]) and _rect_overlap_ratio(line[1], source_rect) > 0
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[1].get_area())


def _replace_mapped_text(text: str, mapping_by_key: dict[str, str]) -> str:
    normalized_chars: list[str] = []
    source_positions: list[int] = []
    for index, char in enumerate(text):
        normalized = matching_key(char)
        normalized_chars.extend(normalized)
        source_positions.extend([index] * len(normalized))
    normalized_text = "".join(normalized_chars)
    keys = sorted(mapping_by_key, key=len, reverse=True)
    replacements: list[tuple[int, int, str]] = []
    cursor = 0
    while cursor < len(normalized_text):
        key = next((item for item in keys if normalized_text.startswith(item, cursor)), None)
        if key is None:
            cursor += 1
            continue
        start = source_positions[cursor]
        end = source_positions[cursor + len(key) - 1] + 1
        replacements.append((start, end, mapping_by_key[key]))
        cursor += len(key)

    result = text
    for start, end, replacement in reversed(replacements):
        result = result[:start] + replacement + result[end:]
    return result


def _rect_overlap_ratio(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    intersection = a & b
    if intersection.is_empty:
        return 0.0
    denominator = max(0.001, min(a.get_area(), b.get_area()))
    return intersection.get_area() / denominator


def _deduplicate_hits(hits: list[_Hit]) -> list[_Hit]:
    result: list[_Hit] = []
    priority = {
        "选区位置": 4,
        "文本层整行重排": 3,
        "文本层全文命中": 2,
        "OCR 独立文字重排": 1,
        "OCR 局部替换": 1,
        "精确替换后 OCR 独立文字重排": 1,
        "精确替换后 OCR 局部替换": 1,
    }
    for hit in sorted(hits, key=lambda item: priority.get(item.kind, 0), reverse=True):
        if any(
            _rect_overlap_ratio(hit.clear_rect, old.clear_rect) > 0.55
            and (
                hit.source_key == old.source_key
                or (hit.reflow_line and old.reflow_line and hit.target == old.target)
            )
            for old in result
        ):
            continue
        result.append(hit)
    return result


def _replace_remaining_ocr_hits(
    output_path: Path,
    mapping_by_key: dict[str, str],
    options: ProcessOptions,
    ocr: RapidOcrEngine,
    progress: ProgressCallback,
    cancel_event: threading.Event | None,
    warnings: list[str],
    log_lines: list[str],
) -> tuple[int, bool]:
    """Find visual old-number remnants after exact stream replacement.

    Any normal Type 3 text occurrences have already been replaced. This pass
    catches old numbers baked into images or converted to vector outlines.
    """
    document = pymupdf.open(output_path)
    hits_by_page: list[list[_Hit]] = []
    total = len(document)
    try:
        for index, page in enumerate(document):
            _check_cancel(cancel_event)
            progress(index, total, f"正在检查全文残留 {index + 1}/{total} 页")
            image = render_page(page, options.global_dpi)
            page_hits: list[_Hit] = []
            for match in find_mapping_matches(ocr.recognize(image), set(mapping_by_key)):
                hit, _ = _ocr_match_hit(
                    page,
                    image,
                    match,
                    mapping_by_key,
                    exact_fallback=True,
                )
                page_hits.append(hit)
            hits_by_page.append(_deduplicate_hits(page_hits))

        count = sum(map(len, hits_by_page))
        if count == 0:
            log_lines.append("精确替换后全文 OCR 残留：0")
            progress(total, total, "全文残留检查完成")
            return 0, True

        all_exact = True
        for index, (page, hits) in enumerate(zip(document, hits_by_page)):
            if not hits:
                continue
            _check_cancel(cancel_event)
            styles = [_detect_style(document, page, hit) for hit in hits]
            for hit in hits:
                clear_rect = pymupdf.Rect(hit.clear_rect)
                clear_rect.x0 -= 0.6
                clear_rect.y0 -= 0.6
                clear_rect.x1 += 0.6
                clear_rect.y1 += 0.6
                page.add_redact_annot(clear_rect, fill=(1, 1, 1), cross_out=False)
            page.apply_redactions(images=2, graphics=0, text=0)
            for hit, style in zip(hits, styles):
                used_size, strategy, exact = _insert_replacement(
                    document, page, hit, style, options
                )
                all_exact = all_exact and exact
                log_lines.append(
                    f"第 {index + 1} 页 {hit.kind}：{hit.target}，字体={strategy}，字号={used_size:.2f}"
                )
                if used_size + 0.05 < style.font_size:
                    warnings.append(
                        f"第 {index + 1} 页 OCR 残留因空间不足，字号由 "
                        f"{style.font_size:.2f} 调整为 {used_size:.2f}"
                    )
            page.clean_contents()

        temp_path = output_path.with_name(output_path.stem + ".ocr-tmp.pdf")
        document.save(temp_path, garbage=4, deflate=True)
        document.close()
        os.replace(temp_path, output_path)
        log_lines.append(f"精确替换后全文 OCR 残留：{count}")
        progress(total, total, "全文残留检查完成")
        return count, all_exact
    finally:
        if not document.is_closed:
            document.close()


@dataclass
class _TextStyle:
    font_size: float
    color: tuple[float, float, float]
    font_name: str = ""


def _detect_style(document: pymupdf.Document, page: pymupdf.Page, hit: _Hit) -> _TextStyle:
    del document
    candidates = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                span_rect = pymupdf.Rect(span["bbox"])
                if _rect_overlap_ratio(span_rect, hit.clear_rect) <= 0:
                    continue
                candidates.append(span)
    if candidates:
        span = max(candidates, key=lambda item: len(item.get("text", "")))
        color_value = int(span.get("color", 0))
        color = (
            ((color_value >> 16) & 255) / 255,
            ((color_value >> 8) & 255) / 255,
            (color_value & 255) / 255,
        )
        return _TextStyle(
            max(4.0, float(span.get("size", 8.0))),
            color,
            str(span.get("font", "")),
        )
    return _TextStyle(max(4.0, hit.display_height * 0.9), (0, 0, 0))


def _insert_replacement(
    document: pymupdf.Document,
    page: pymupdf.Page,
    hit: _Hit,
    style: _TextStyle,
    options: ProcessOptions,
) -> tuple[float, str, bool]:
    if not hit.target:
        return style.font_size, "清除文字", True
    has_cjk = any(ord(char) > 255 for char in hit.target)
    fontname = "china-s" if has_cjk else "helv"
    strategy = "PyMuPDF 内置中文字体" if has_cjk else "PyMuPDF Helvetica 回退字体"
    exact_font = False
    measure_font = pymupdf.Font("china-s" if has_cjk else "helv")
    embedded = None
    if options.replacement_font_path:
        font_path = Path(options.replacement_font_path)
        if not font_path.is_file():
            raise ValueError(f"所选 Windows 字体文件不存在：{font_path}")
        try:
            measure_font = pymupdf.Font(fontfile=str(font_path))
        except Exception as exc:
            raise ValueError(f"无法读取所选 Windows 字体：{options.replacement_font_name}") from exc
        missing = sorted({char for char in hit.target if not measure_font.has_glyph(ord(char))})
        if missing:
            raise ValueError(
                f"所选 Windows 字体 {options.replacement_font_name} 缺少字符：{''.join(missing)}"
            )
        fontname = "FWindows"
        page.insert_font(fontname=fontname, fontfile=str(font_path))
        strategy = f"Windows 字体 {options.replacement_font_name}"
    else:
        embedded = _embedded_font(document, page, style.font_name, hit.target)
    if embedded is not None:
        xref, font_buffer, base_name = embedded
        fontname = f"FRepl{xref}"
        page.insert_font(fontname=fontname, fontbuffer=font_buffer)
        measure_font = pymupdf.Font(fontbuffer=font_buffer)
        strategy = f"原嵌入字体 {base_name}"
        exact_font = True
    elif style.font_name:
        base14 = _base14_font(style.font_name)
        if base14 is not None and not has_cjk:
            fontname = base14
            measure_font = pymupdf.Font(base14)
            strategy = f"原标准字体 {style.font_name}"
            exact_font = True
    rotation = page.rotation if page.rotation in (0, 90, 180, 270) else 0
    font_size = style.font_size
    if not hit.reflow_line:
        while font_size >= 4.0:
            text_width = measure_font.text_length(hit.target, fontsize=font_size)
            bounds = _centered_text_bounds(
                pymupdf.Rect(hit.placement_rect),
                measure_font,
                font_size,
                text_width,
                rotation,
            )
            if page.mediabox.contains(bounds):
                page.insert_text(
                    _centered_text_origin(
                        pymupdf.Rect(hit.placement_rect),
                        measure_font,
                        font_size,
                        text_width,
                        rotation,
                    ),
                    hit.target,
                    fontname=fontname,
                    fontsize=font_size,
                    color=style.color,
                    rotate=rotation,
                    overlay=True,
                )
                return font_size, strategy, exact_font and abs(font_size - style.font_size) < 0.05
            font_size -= 0.25
        raise RuntimeError(f"Replacement text does not fit on page {page.number + 1}")

    rect = pymupdf.Rect(hit.placement_rect)
    text_width = measure_font.text_length(hit.target, fontsize=style.font_size) * 1.03
    rect = _line_layout_rect(
        rect,
        text_width,
        style.font_size * 1.8,
        rotation,
    )
    rect = rect & page.mediabox
    while font_size >= 4.0:
        result = page.insert_textbox(
            rect,
            hit.target,
            fontname=fontname,
            fontsize=font_size,
            color=style.color,
            align=pymupdf.TEXT_ALIGN_LEFT if hit.reflow_line else pymupdf.TEXT_ALIGN_CENTER,
            rotate=rotation,
            overlay=True,
        )
        if result >= 0:
            return font_size, strategy, exact_font and abs(font_size - style.font_size) < 0.05
        font_size -= 0.25
    raise RuntimeError(f"Replacement text does not fit on page {page.number + 1}")


def _centered_text_origin(
    placement: pymupdf.Rect,
    font: pymupdf.Font,
    font_size: float,
    text_width: float,
    rotation: int,
) -> pymupdf.Point:
    center = (placement.tl + placement.br) / 2
    baseline_offset = (float(font.ascender) + float(font.descender)) * font_size / 2
    if rotation == 0:
        return pymupdf.Point(center.x - text_width / 2, center.y + baseline_offset)
    if rotation == 180:
        return pymupdf.Point(center.x + text_width / 2, center.y - baseline_offset)
    if rotation == 90:
        return pymupdf.Point(center.x + baseline_offset, center.y + text_width / 2)
    return pymupdf.Point(center.x - baseline_offset, center.y - text_width / 2)


def _centered_text_bounds(
    placement: pymupdf.Rect,
    font: pymupdf.Font,
    font_size: float,
    text_width: float,
    rotation: int,
) -> pymupdf.Rect:
    glyph_height = max(0.1, float(font.ascender) - float(font.descender)) * font_size
    if rotation in (0, 180):
        return _centered_rect(placement, text_width, glyph_height)
    return _centered_rect(placement, glyph_height, text_width)


def _centered_rect(rect: pymupdf.Rect, width: float, height: float) -> pymupdf.Rect:
    center = (rect.tl + rect.br) / 2
    return pymupdf.Rect(
        center.x - width / 2,
        center.y - height / 2,
        center.x + width / 2,
        center.y + height / 2,
    )


def _line_layout_rect(
    rect: pymupdf.Rect,
    text_width: float,
    text_height: float,
    rotation: int,
) -> pymupdf.Rect:
    center = (rect.tl + rect.br) / 2
    if rotation == 0:
        return pymupdf.Rect(
            rect.x0,
            center.y - max(rect.height, text_height) / 2,
            rect.x0 + max(rect.width, text_width),
            center.y + max(rect.height, text_height) / 2,
        )
    if rotation == 180:
        return pymupdf.Rect(
            rect.x1 - max(rect.width, text_width),
            center.y - max(rect.height, text_height) / 2,
            rect.x1,
            center.y + max(rect.height, text_height) / 2,
        )
    if rotation == 90:
        return pymupdf.Rect(
            center.x - max(rect.width, text_height) / 2,
            rect.y0,
            center.x + max(rect.width, text_height) / 2,
            rect.y0 + max(rect.height, text_width),
        )
    return pymupdf.Rect(
        center.x - max(rect.width, text_height) / 2,
        rect.y1 - max(rect.height, text_width),
        center.x + max(rect.width, text_height) / 2,
        rect.y1,
    )


def _font_key(name: str) -> str:
    name = name.split("+", 1)[-1]
    return "".join(char.lower() for char in name if char.isalnum())


def _embedded_font(
    document: pymupdf.Document,
    page: pymupdf.Page,
    style_name: str,
    target: str,
) -> tuple[int, bytes, str] | None:
    if not style_name:
        return None
    wanted = _font_key(style_name)
    for info in page.get_fonts(full=True):
        xref, _, font_type, base_name, resource_name = info[:5]
        if wanted not in {_font_key(str(base_name)), _font_key(str(resource_name))}:
            continue
        extracted = document.extract_font(xref)
        font_buffer = extracted[3]
        if not font_buffer or str(font_type) == "Type3":
            continue
        try:
            font = pymupdf.Font(fontbuffer=font_buffer)
            if not all(font.has_glyph(ord(char)) for char in target):
                continue
        except Exception:
            continue
        return int(xref), font_buffer, str(base_name)
    return None


def _base14_font(style_name: str) -> str | None:
    key = _font_key(style_name)
    if "courier" in key:
        return "cour"
    if "helvetica" in key or "arial" in key:
        return "helv"
    if "times" in key:
        return "tiro"
    return None


def _write_log(
    log_path: Path,
    source_path: Path,
    selection: NormalizedRect,
    recognitions: list[PageRecognition],
    options: ProcessOptions,
    report: ProcessReport,
    detail_lines: list[str],
) -> None:
    lines = [
        "EditNUM 处理日志",
        f"软件版本：{__version__}",
        f"处理时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"源文件：{source_path}",
        f"输出文件：{report.output_path}",
        f"页数：{report.page_count}",
        "选择区域：" + ",".join(f"{value:.6f}" for value in selection.as_tuple()),
        f"固定前缀：{options.fixed_prefix or '（空）'}",
        (
            f"递增序号：从 {options.start_sequence} 开始，{options.sequence_digits} 位"
            if options.start_sequence is not None
            else "递增序号：未启用"
        ),
        f"固定后缀：{options.fixed_suffix or '（空）'}",
        f"替换字体：{options.replacement_font_name or '自动匹配原字体'}",
        f"选区 OCR DPI：{options.fixed_dpi}",
        f"全文相同文字替换：{'是' if options.global_search else '否'}",
        f"全文 OCR DPI：{options.global_dpi}",
        "",
        "[各页选区识别]",
    ]
    lines.extend(f"第 {item.page_number} 页 | {item.source}" for item in recognitions)
    lines.extend(["", "[替换对应关系]"])
    lines.extend(
        f"{entry.source} -> {entry.target} | 页码 {','.join(map(str, entry.pages))}"
        for entry in report.mappings
    )
    lines.extend(
        [
            "",
            "[执行结果]",
            f"选区位置替换：{report.fixed_replacements}",
            f"全文相同文字替换：{report.global_replacements}",
            f"精确保留嵌入字体：{'是' if report.exact_font_preserved else '否'}",
        ]
    )
    lines.extend(detail_lines)
    lines.extend(["", "[警告]"])
    lines.extend(report.warnings or ["无"])
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
