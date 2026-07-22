from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import numpy as np
from PIL import Image

from .models import OcrSpan, clean_ocr_text, matching_key


class OcrError(RuntimeError):
    pass


class RapidOcrEngine:
    """Lazy, offline RapidOCR wrapper with a stable internal result format."""

    def __init__(self) -> None:
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            from rapidocr import RapidOCR

            self._engine = RapidOCR()
        return self._engine

    def recognize(self, image: Image.Image) -> list[OcrSpan]:
        rgb = image.convert("RGB")
        result = self._get_engine()(np.asarray(rgb))
        boxes = getattr(result, "boxes", None)
        texts = getattr(result, "txts", None)
        scores = getattr(result, "scores", None)

        # RapidOCR 1.x returned (result, elapsed), while 3.x exposes attributes.
        if boxes is None and isinstance(result, tuple) and result:
            rows = result[0] or []
            spans = []
            for box, text, score in rows:
                spans.append(OcrSpan(str(text), float(score), _bounds(box)))
            return spans
        if boxes is None or texts is None or scores is None:
            return []
        return [
            OcrSpan(str(text), float(score), _bounds(box))
            for box, text, score in zip(boxes, texts, scores)
        ]


def _bounds(points) -> tuple[float, float, float, float]:
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _line_groups(spans: Iterable[OcrSpan]) -> list[list[OcrSpan]]:
    rows: list[list[OcrSpan]] = []
    for span in sorted(spans, key=lambda item: ((item.box[1] + item.box[3]) / 2, item.box[0])):
        center = (span.box[1] + span.box[3]) / 2
        height = max(1.0, span.box[3] - span.box[1])
        for row in rows:
            row_center = sum((item.box[1] + item.box[3]) / 2 for item in row) / len(row)
            row_height = max(item.box[3] - item.box[1] for item in row)
            if abs(center - row_center) <= max(height, row_height) * 0.6:
                row.append(span)
                break
        else:
            rows.append([span])
    for row in rows:
        row.sort(key=lambda item: item.box[0])
    return rows


def primary_text(spans: list[OcrSpan]) -> tuple[str, float, tuple[float, float, float, float]]:
    candidates = []
    for row in _line_groups(spans):
        text = clean_ocr_text("".join(item.text for item in row))
        if not text:
            continue
        total_chars = sum(max(1, len(clean_ocr_text(item.text))) for item in row)
        confidence = sum(
            item.confidence * max(1, len(clean_ocr_text(item.text))) for item in row
        ) / total_chars
        box = _union_boxes(item.box for item in row)
        candidates.append((text, confidence, box))
    if not candidates:
        raise OcrError("No text was recognized in the selected area")
    return max(candidates, key=lambda item: (len(item[0]), item[1]))


@dataclass(frozen=True)
class OcrMatch:
    key: str
    confidence: float
    box: tuple[float, float, float, float]


def find_mapping_matches(spans: list[OcrSpan], known_keys: set[str]) -> list[OcrMatch]:
    matches: list[OcrMatch] = []
    for span in spans:
        normalized = matching_key(span.text)
        for key in known_keys:
            offset = normalized.find(key)
            if offset < 0:
                continue
            x0, y0, x1, y1 = span.box
            width_per_char = (x1 - x0) / max(1, len(normalized))
            matches.append(
                OcrMatch(
                    key=key,
                    confidence=span.confidence,
                    box=(
                        x0 + offset * width_per_char,
                        y0,
                        x0 + (offset + len(key)) * width_per_char,
                        y1,
                    ),
                )
            )
    for row in _line_groups(spans):
        normalized = [matching_key(span.text) for span in row]
        for start in range(len(row)):
            combined = ""
            weighted_score = 0.0
            weighted_chars = 0
            for end in range(start, min(len(row), start + 12)):
                part = normalized[end]
                if not part:
                    continue
                combined += part
                chars = len(part)
                weighted_score += row[end].confidence * chars
                weighted_chars += chars
                if combined in known_keys:
                    matches.append(
                        OcrMatch(
                            key=combined,
                            confidence=weighted_score / weighted_chars,
                            box=_union_boxes(item.box for item in row[start : end + 1]),
                        )
                    )
                if not any(key.startswith(combined) for key in known_keys):
                    break
    return _deduplicate_matches(matches)


def _union_boxes(boxes: Iterable[tuple[float, float, float, float]]):
    boxes = list(boxes)
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _deduplicate_matches(matches: list[OcrMatch]) -> list[OcrMatch]:
    result: list[OcrMatch] = []
    for match in sorted(matches, key=lambda item: item.confidence, reverse=True):
        if any(match.key == old.key and _iou(match.box, old.box) > 0.6 for old in result):
            continue
        result.append(match)
    return result


def _iou(a, b) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if math.isclose(intersection, 0.0):
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return intersection / (area_a + area_b - intersection)
