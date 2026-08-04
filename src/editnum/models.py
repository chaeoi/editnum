from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import re
import unicodedata


@dataclass(frozen=True)
class NormalizedRect:
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        values = (self.x0, self.y0, self.x1, self.y1)
        if not all(0.0 <= value <= 1.0 for value in values):
            raise ValueError("Selection coordinates must be between 0 and 1")
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("Selection must have a positive width and height")

    def as_tuple(self) -> tuple[float, float, float, float]:
        return self.x0, self.y0, self.x1, self.y1


@dataclass(frozen=True)
class OcrSpan:
    text: str
    confidence: float
    box: tuple[float, float, float, float]


@dataclass
class PageRecognition:
    page_number: int
    source: str
    text_rect: NormalizedRect
    raw_text: str = ""


@dataclass(frozen=True)
class MappingEntry:
    source: str
    target: str
    pages: tuple[int, ...]


@dataclass
class ProcessOptions:
    fixed_prefix: str = ""
    fixed_suffix: str = ""
    start_sequence: int | None = 1
    sequence_digits: int = 3
    fixed_dpi: int = 300
    global_dpi: int = 180
    global_search: bool = False
    replacement_font_path: str = ""
    replacement_font_name: str = ""
    fixed_font_size: float | None = None
    global_font_size: float | None = None

    def validate(self) -> None:
        if self.start_sequence is not None and self.start_sequence < 0:
            raise ValueError("起始序号不能为负数")
        if not 1 <= self.sequence_digits <= 12:
            raise ValueError("递增序号位数必须在 1 到 12 之间")
        if not 144 <= self.fixed_dpi <= 600:
            raise ValueError("区域 DPI 必须在 144 到 600 之间")
        if not 96 <= self.global_dpi <= 300:
            raise ValueError("全文 OCR DPI 必须在 96 到 300 之间")
        for label, font_size in (
            ("选区字号", self.fixed_font_size),
            ("其他区域字号", self.global_font_size),
        ):
            if font_size is not None and not 1.0 <= font_size <= 200.0:
                raise ValueError(f"{label}必须在 1 到 200 磅之间")

    def number_for(self, sequence: int) -> str:
        self.validate()
        increment = ""
        if self.start_sequence is not None:
            increment = f"{sequence:0{self.sequence_digits}d}"
        return f"{self.fixed_prefix}{increment}{self.fixed_suffix}"

    @property
    def rule_text(self) -> str:
        increment = f"{{递增:{self.sequence_digits}位}}" if self.start_sequence is not None else ""
        return f"{self.fixed_prefix}{increment}{self.fixed_suffix}"


@dataclass
class ProcessReport:
    output_path: str
    log_path: str
    page_count: int
    mappings: list[MappingEntry]
    fixed_replacements: int
    global_replacements: int
    exact_font_preserved: bool
    warnings: list[str] = field(default_factory=list)


_DASHES = str.maketrans({
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "−": "-",
    "－": "-",
})


def clean_ocr_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_DASHES)
    return re.sub(r"\s+", "", text).strip()


def matching_key(text: str) -> str:
    return clean_ocr_text(text).upper()


def build_mapping(
    recognitions: list[PageRecognition], options: ProcessOptions
) -> tuple[list[MappingEntry], dict[str, str]]:
    options.validate()
    grouped: OrderedDict[str, dict[str, object]] = OrderedDict()
    for recognition in sorted(recognitions, key=lambda item: item.page_number):
        source = clean_ocr_text(recognition.source)
        if not source:
            raise ValueError(f"第 {recognition.page_number} 页的识别文字为空")
        key = matching_key(source)
        if key not in grouped:
            grouped[key] = {"source": source, "pages": []}
        grouped[key]["pages"].append(recognition.page_number)  # type: ignore[union-attr]

    entries: list[MappingEntry] = []
    lookup: dict[str, str] = {}
    for offset, (key, item) in enumerate(grouped.items()):
        sequence = (options.start_sequence or 0) + offset
        target = options.number_for(sequence)
        entry = MappingEntry(
            source=str(item["source"]),
            target=target,
            pages=tuple(item["pages"]),  # type: ignore[arg-type]
        )
        entries.append(entry)
        lookup[key] = target
    return entries, lookup
