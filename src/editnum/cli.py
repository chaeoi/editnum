#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from .models import NormalizedRect, ProcessOptions
from .ocr import RapidOcrEngine
from .processor import process_document, recognize_fixed_regions


def _selection(value: str) -> NormalizedRect:
    try:
        numbers = [float(item) for item in value.split(",")]
        if len(numbers) != 4:
            raise ValueError
        return NormalizedRect(*numbers)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("区域格式应为 x0,y0,x1,y1，取值 0 到 1") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="离线识别并批量替换 PDF 文字")
    parser.add_argument("source", type=Path, help="源 PDF")
    parser.add_argument("--rect", required=True, type=_selection, help="归一化文字选区")
    parser.add_argument("-o", "--output", type=Path, help="输出 PDF")
    parser.add_argument("--prefix", default="", help="固定前缀，可为空")
    parser.add_argument("--start", type=int, default=1, help="起始序号")
    parser.add_argument("--digits", type=int, default=3, help="递增序号位数")
    parser.add_argument("--no-sequence", action="store_true", help="不使用递增序号")
    parser.add_argument("--suffix", default="", help="固定后缀，可为空")
    parser.add_argument("--font-file", type=Path, help="替换时使用的 TTF/TTC/OTF 字体文件")
    parser.add_argument("--fixed-dpi", type=int, default=300)
    parser.add_argument("--document-dpi", "--global-dpi", dest="global_dpi", type=int, default=180)
    parser.add_argument(
        "--replace-all", action="store_true", help="同时替换全文中的相同文字"
    )
    args = parser.parse_args()

    output = args.output or args.source.with_name(args.source.stem + "_文字已替换.pdf")
    options = ProcessOptions(
        fixed_prefix=args.prefix,
        fixed_suffix=args.suffix,
        start_sequence=None if args.no_sequence else args.start,
        sequence_digits=args.digits,
        fixed_dpi=args.fixed_dpi,
        global_dpi=args.global_dpi,
        global_search=args.replace_all,
        replacement_font_path=str(args.font_file or ""),
        replacement_font_name=args.font_file.stem if args.font_file else "",
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
