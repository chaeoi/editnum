#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from drawing_number_tool.models import NormalizedRect, ProcessOptions
from drawing_number_tool.ocr import RapidOcrEngine
from drawing_number_tool.processor import process_document, recognize_fixed_regions


def _selection(value: str) -> NormalizedRect:
    try:
        numbers = [float(item) for item in value.split(",")]
        if len(numbers) != 4:
            raise ValueError
        return NormalizedRect(*numbers)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("区域格式应为 x0,y0,x1,y1，取值 0 到 1") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="离线识别并替换 PDF 图号")
    parser.add_argument("source", type=Path, help="源 PDF")
    parser.add_argument("--rect", required=True, type=_selection, help="归一化图号区域")
    parser.add_argument("-o", "--output", type=Path, help="输出 PDF")
    parser.add_argument(
        "--template",
        default="00QK0188RJB-LJ-{seq:03d}-0-260715",
        help="新图号模板，必须包含 {seq}",
    )
    parser.add_argument("--start", type=int, default=1, help="起始序号")
    parser.add_argument("--fixed-dpi", type=int, default=300)
    parser.add_argument("--global-dpi", type=int, default=180)
    parser.add_argument("--no-global-search", action="store_true")
    args = parser.parse_args()

    output = args.output or args.source.with_name(args.source.stem + "_图号已修改.pdf")
    options = ProcessOptions(
        template=args.template,
        start_sequence=args.start,
        fixed_dpi=args.fixed_dpi,
        global_dpi=args.global_dpi,
        global_search=not args.no_global_search,
    )

    def progress(current: int, total: int, message: str) -> None:
        print(f"[{current}/{total}] {message}", flush=True)

    ocr = RapidOcrEngine()
    recognitions = recognize_fixed_regions(
        args.source, args.rect, options, ocr=ocr, progress=progress
    )
    report = process_document(
        args.source,
        output,
        args.rect,
        recognitions,
        options,
        ocr=ocr,
        progress=progress,
    )
    print(f"PDF: {report.output_path}")
    print(f"LOG: {report.log_path}")


if __name__ == "__main__":
    main()
