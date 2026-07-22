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
    confidence: float
    text_rect: NormalizedRect
    raw_text: str = ""


@dataclass(frozen=True)
class MappingEntry:
    source: str
    target: str
    pages: tuple[int, ...]


@dataclass
class ProcessOptions:
    template: str = "00QK0188RJB-LJ-{seq:03d}-0-260715"
    start_sequence: int = 1
    fixed_dpi: int = 300
    global_dpi: int = 180
    global_search: bool = True
    minimum_confidence: float = 0.75

    def validate(self) -> None:
        if self.start_sequence < 0:
            raise ValueError("The starting sequence cannot be negative")
        if not 144 <= self.fixed_dpi <= 600:
            raise ValueError("Fixed-region OCR DPI must be between 144 and 600")
        if not 96 <= self.global_dpi <= 300:
            raise ValueError("Full-page OCR DPI must be between 96 and 300")
        try:
            first = self.template.format(seq=self.start_sequence)
            second = self.template.format(seq=self.start_sequence + 1)
        except (KeyError, ValueError, IndexError) as exc:
            raise ValueError("Template must contain a valid {seq} field") from exc
        if first == second:
            raise ValueError("Template must contain a {seq} field that changes the result")


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
            raise ValueError(f"Page {recognition.page_number} has an empty drawing number")
        key = matching_key(source)
        if key not in grouped:
            grouped[key] = {"source": source, "pages": []}
        grouped[key]["pages"].append(recognition.page_number)  # type: ignore[union-attr]

    entries: list[MappingEntry] = []
    lookup: dict[str, str] = {}
    for offset, (key, item) in enumerate(grouped.items()):
        target = options.template.format(seq=options.start_sequence + offset)
        entry = MappingEntry(
            source=str(item["source"]),
            target=target,
            pages=tuple(item["pages"]),  # type: ignore[arg-type]
        )
        entries.append(entry)
        lookup[key] = target
    return entries, lookup
