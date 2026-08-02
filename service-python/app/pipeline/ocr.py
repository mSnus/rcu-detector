"""OCR behind a swappable engine interface.

OCR is the most fragile dependency in this stack, so no pipeline module ever
imports an engine directly. Everything goes through `run_ocr(crop)`, which
returns plain dicts in normalised crop coordinates.

Engines are adapters registered in `_ADAPTERS`. `get_engine()` walks
`CFG.ocr.engine_order` and returns the first one that actually imports, so a
box with only Tesseract installed still works, and adding an engine is one
class plus one registry line.

For the offline catalog build, `run_ocr(crop, consensus=True)` runs every
installed engine and keeps a region only where engines agree or one engine is
very confident. The query path uses a single engine and stays fast.

Nothing downstream may *require* a label to be present. Label recall on phone
photos of black remotes is low, and geometry has to carry the match on its
own -- labels are a bonus scoring term, only ever.
"""
from __future__ import annotations

import re
from typing import Iterable

import cv2
import numpy as np
from rapidfuzz import fuzz

from app.config import CFG

# ---------------------------------------------------------------------------
# text region representation
#
# A region is a dict, not a class, so it serialises into the fingerprint
# unchanged:
#   text        recognised string, upper-cased and cleaned
#   conf        0..1
#   x, y, w, h  axis-aligned box, centre + size, normalised to the crop
#   angle       degrees, from the quadrilateral's long edge
#   engines     which engines reported it
# ---------------------------------------------------------------------------

_CLEAN_RE = re.compile(r"[^A-Z0-9/:.+\-]")


def clean_text(raw: str) -> str:
    """Normalise a recognition for comparison and storage.

    Upper-cases and drops decoration OCR invents around glyph buttons. Keeps
    the punctuation that appears in real labels and model codes: RM-D1110,
    16:9, P/S, VOL+.
    """
    return _CLEAN_RE.sub("", (raw or "").upper().strip())


def _region(poly, text: str, conf: float, W: int, H: int,
            engine: str) -> dict | None:
    """Build a normalised region from an engine's quadrilateral."""
    txt = clean_text(text)
    if not txt:
        return None
    pts = np.asarray(poly, dtype=np.float32).reshape(-1, 2)
    xs, ys = pts[:, 0], pts[:, 1]
    x1, x2 = float(xs.min()), float(xs.max())
    y1, y2 = float(ys.min()), float(ys.max())

    # angle of the top edge, when the engine gives an ordered quad
    angle = 0.0
    if len(pts) == 4:
        dx, dy = pts[1] - pts[0]
        angle = float(np.degrees(np.arctan2(dy, dx)))
        if angle > 90:
            angle -= 180
        elif angle < -90:
            angle += 180

    return {
        "text": txt,
        "conf": round(float(conf), 3),
        "x": round((x1 + x2) / 2 / W, 4),
        "y": round((y1 + y2) / 2 / H, 4),
        "w": round((x2 - x1) / W, 4),
        "h": round((y2 - y1) / H, 4),
        "angle": round(angle, 1),
        "engines": [engine],
    }


# ---------------------------------------------------------------------------
# engine adapters
# ---------------------------------------------------------------------------

class OcrEngine:
    """Adapter contract: `available()` must not raise, `read()` returns
    regions in the coordinate space of the image it was handed."""

    name = "base"

    @classmethod
    def available(cls) -> bool:
        raise NotImplementedError

    def read(self, img: np.ndarray) -> list[dict]:
        raise NotImplementedError


class RapidOcrEngine(OcrEngine):
    """PP-OCR detection + recognition under onnxruntime.

    Same model family as PaddleOCR, ~150 MB rather than ~1 GB, no paddle
    runtime. This is the default.
    """

    name = "rapidocr"

    @classmethod
    def available(cls) -> bool:
        try:
            import rapidocr_onnxruntime  # noqa: F401
            return True
        except Exception:
            return False

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR
        n = CFG.ocr.threads
        self._ocr = RapidOCR(
            det_intra_op_num_threads=n, det_inter_op_num_threads=n,
            cls_intra_op_num_threads=n, cls_inter_op_num_threads=n,
            rec_intra_op_num_threads=n, rec_inter_op_num_threads=n)

    def read(self, img: np.ndarray) -> list[dict]:
        H, W = img.shape[:2]
        # use_cls must follow CFG.ocr.angle_cls -- see the note there. Leaving
        # it at RapidOCR's default of True zeroes the orientation signal.
        result, _ = self._ocr(img, use_cls=CFG.ocr.angle_cls)
        out = []
        for box, text, score in (result or []):
            r = _region(box, text, score, W, H, self.name)
            if r:
                out.append(r)
        return out


class PaddleOcrEngine(OcrEngine):
    """The engine the implementation plan specifies. Heavy, but the reference."""

    name = "paddle"

    @classmethod
    def available(cls) -> bool:
        try:
            import paddleocr  # noqa: F401
            return True
        except Exception:
            return False

    def __init__(self) -> None:
        from paddleocr import PaddleOCR
        # Same trap as RapidOCR: paddle defaults the angle classifier on and
        # it destroys the orientation signal. See CFG.ocr.angle_cls. Read at
        # construction, so a config change needs a fresh engine instance.
        self._ocr = PaddleOCR(use_angle_cls=CFG.ocr.angle_cls, lang="en",
                              show_log=False)

    def read(self, img: np.ndarray) -> list[dict]:
        H, W = img.shape[:2]
        raw = self._ocr.ocr(img, cls=CFG.ocr.angle_cls) or []
        # paddle nests one list per image
        lines = raw[0] if raw and isinstance(raw[0], list) else raw
        out = []
        for entry in lines or []:
            try:
                box, (text, score) = entry[0], entry[1]
            except Exception:
                continue
            r = _region(box, text, score, W, H, self.name)
            if r:
                out.append(r)
        return out


class EasyOcrEngine(OcrEngine):
    name = "easyocr"

    @classmethod
    def available(cls) -> bool:
        try:
            import easyocr  # noqa: F401
            return True
        except Exception:
            return False

    def __init__(self) -> None:
        import easyocr
        self._ocr = easyocr.Reader(["en"], gpu=False, verbose=False)

    def read(self, img: np.ndarray) -> list[dict]:
        H, W = img.shape[:2]
        out = []
        for box, text, score in self._ocr.readtext(img):
            r = _region(box, text, score, W, H, self.name)
            if r:
                out.append(r)
        return out


class DocTrEngine(OcrEngine):
    name = "doctr"

    @classmethod
    def available(cls) -> bool:
        try:
            from doctr.models import ocr_predictor  # noqa: F401
            return True
        except Exception:
            return False

    def __init__(self) -> None:
        from doctr.models import ocr_predictor
        self._ocr = ocr_predictor(pretrained=True)

    def read(self, img: np.ndarray) -> list[dict]:
        H, W = img.shape[:2]
        # docTR wants RGB and reports geometry in relative coordinates
        res = self._ocr([cv2.cvtColor(img, cv2.COLOR_BGR2RGB)])
        out = []
        for page in res.pages:
            for block in page.blocks:
                for line in block.lines:
                    for word in line.words:
                        (rx1, ry1), (rx2, ry2) = word.geometry
                        poly = [[rx1 * W, ry1 * H], [rx2 * W, ry1 * H],
                                [rx2 * W, ry2 * H], [rx1 * W, ry2 * H]]
                        r = _region(poly, word.value, word.confidence, W, H,
                                    self.name)
                        if r:
                            out.append(r)
        return out


class TesseractEngine(OcrEngine):
    """Last resort. Weakest of the four on remote-control labels, but it is
    packaged everywhere and needs no model download."""

    name = "tesseract"

    @classmethod
    def available(cls) -> bool:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return False

    def __init__(self) -> None:
        import pytesseract
        self._pt = pytesseract

    def read(self, img: np.ndarray) -> list[dict]:
        H, W = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        data = self._pt.image_to_data(
            gray, config="--psm 11", output_type=self._pt.Output.DICT)
        out = []
        for i, text in enumerate(data["text"]):
            try:
                conf = float(data["conf"][i]) / 100.0
            except (TypeError, ValueError):
                continue
            if conf <= 0:
                continue
            x, y = data["left"][i], data["top"][i]
            w, h = data["width"][i], data["height"][i]
            poly = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
            r = _region(poly, text, conf, W, H, self.name)
            if r:
                out.append(r)
        return out


_ADAPTERS: dict[str, type[OcrEngine]] = {
    "rapidocr": RapidOcrEngine,
    "paddle": PaddleOcrEngine,
    "easyocr": EasyOcrEngine,
    "doctr": DocTrEngine,
    "tesseract": TesseractEngine,
}

_INSTANCES: dict[str, OcrEngine] = {}


def available_engines() -> list[str]:
    """Installed engines, in preference order."""
    return [n for n in CFG.ocr.engine_order
            if n in _ADAPTERS and _ADAPTERS[n].available()]


def get_engine(name: str | None = None) -> OcrEngine:
    """Return a cached engine instance.

    With no name, the first installed engine in preference order. Engines load
    model weights on construction, so they are built once per process.
    """
    if name is None:
        names = available_engines()
        if not names:
            raise RuntimeError(
                "no OCR engine installed. Run install.sh, or set "
                "OCR_EXTRA=paddle|easyocr|doctr|tesseract to add one.")
        name = names[0]
    if name not in _INSTANCES:
        if name not in _ADAPTERS:
            raise KeyError(f"unknown OCR engine: {name}")
        _INSTANCES[name] = _ADAPTERS[name]()
    return _INSTANCES[name]


# ---------------------------------------------------------------------------
# running OCR
# ---------------------------------------------------------------------------

def _prepare(crop: np.ndarray, min_width: int | None = None) -> np.ndarray:
    """Upscale for OCR.

    The canonical crop is 400px wide, which renders VOL about six pixels tall
    -- below what any of these engines will read. Upscaling before detection
    costs nothing offline and is the single biggest lever on label recall.

    `min_width` overrides the configured upscale for callers that trade label
    recall for latency -- see `OcrConfig.query_min_width`.
    """
    W = crop.shape[1]
    target = min_width or CFG.ocr.min_width
    if W >= target:
        return crop
    scale = target / float(W)
    return cv2.resize(crop, (target, int(round(crop.shape[0] * scale))),
                      interpolation=cv2.INTER_CUBIC)


def _iou(a: dict, b: dict) -> float:
    ax1, ay1 = a["x"] - a["w"] / 2, a["y"] - a["h"] / 2
    ax2, ay2 = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2
    bx1, by1 = b["x"] - b["w"] / 2, b["y"] - b["h"] / 2
    bx2, by2 = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    ix = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / max(union, 1e-9)


def _bands(H: int) -> list[tuple[int, int]]:
    """Split a crop of height H into overlapping horizontal bands.

    A remote upscaled for OCR is tall and narrow -- around 1100x4500 -- and
    running text detection over the whole thing at once needs more memory than
    a small VM has (this OOM-killed the first run). Bands bound peak memory
    and cost nothing in accuracy as long as they overlap by more than the
    tallest line of text.
    """
    cfg = CFG.ocr
    if H <= cfg.tile_max_height:
        return [(0, H)]
    n = int(np.ceil(H / cfg.tile_max_height))
    step = H / n
    over = int(step * cfg.tile_overlap)
    out = []
    for i in range(n):
        y1 = max(0, int(i * step) - over)
        y2 = min(H, int((i + 1) * step) + over)
        out.append((y1, y2))
    return out


def _dedupe(regions: list[dict]) -> list[dict]:
    """Drop the duplicate readings produced in band overlaps."""
    kept: list[dict] = []
    for r in sorted(regions, key=lambda r: -r["conf"]):
        if any(_iou(r, k) > CFG.ocr.dup_iou for k in kept):
            continue
        kept.append(r)
    return kept


def _read_banded(eng: "OcrEngine", img: np.ndarray) -> list[dict]:
    """Run one engine over the crop band by band, in whole-crop coordinates."""
    H, W = img.shape[:2]
    out: list[dict] = []
    for y1, y2 in _bands(H):
        band = img[y1:y2]
        bh = band.shape[0]
        for r in eng.read(band):
            # band-relative -> crop-relative
            r["y"] = round((r["y"] * bh + y1) / H, 4)
            r["h"] = round(r["h"] * bh / H, 4)
            out.append(r)
    return _dedupe(out)


def merge_engine_results(per_engine: Iterable[list[dict]]) -> list[dict]:
    """Fuse several engines' regions, keeping the agreed-upon ones.

    A region survives when `consensus_min_engines` engines found it and read
    the same string, or when one engine reported above `solo_conf`. Boxes are
    matched by IoU; the text of the highest-confidence reading wins.
    """
    cfg = CFG.ocr
    clusters: list[list[dict]] = []
    for regions in per_engine:
        for r in regions:
            for cl in clusters:
                if _iou(cl[0], r) > cfg.match_iou:
                    cl.append(r)
                    break
            else:
                clusters.append([r])

    out = []
    for cl in clusters:
        best = max(cl, key=lambda r: r["conf"])
        agreeing = {r["engines"][0] for r in cl if r["text"] == best["text"]}
        if len(agreeing) >= cfg.consensus_min_engines or best["conf"] >= cfg.solo_conf:
            merged = dict(best)
            merged["engines"] = sorted({e for r in cl for e in r["engines"]})
            merged["agree"] = len(agreeing)
            out.append(merged)
    return out


def drop_watermark(regions: list[dict]) -> list[dict]:
    """Remove the source site's watermark from the OCR output.

    Applied here, at the boundary, so that nothing downstream can see it. The
    stamp would otherwise become index tokens, be assigned as a button label,
    be read as a brand, and feed the text orientation signal -- which it would
    always resolve upright, because the stamp is upright whichever way up the
    remote is lying. That last one is the dangerous one: CLAUDE.md's rule is
    that orientation errors are silent and corrupting.

    Three tests, all required, because no one of them is safe alone:

      - **fuzzy similarity**, because a semi-transparent stamp over a keypad
        OCRs in fragments and with errors ("OV.NET", "PULTCV", "ET");
      - **the height ratio**, because "EXIT" scores 67 against "PULTOVNET" and
        "NETFLIX" scores 60, and both are real legends sitting in the same
        band. A stamp is set 1.4x-5.5x the median legend; EXIT and NETFLIX are
        1.2x and below;
      - **the band**, because "NEXT" scores 86 and lives at the bottom of a
        remote, nowhere near a centred stamp.

    The median is taken over the crop's own regions, so it travels between the
    catalog and query paths even though they read different numbers of them.
    """
    cfg = CFG.ocr
    if not cfg.watermark_filter or not cfg.watermark_terms or not regions:
        return regions

    heights = sorted(r.get("h", 0.0) for r in regions)
    median_h = heights[len(heights) // 2] or 1e-6

    kept = []
    for r in regions:
        bare = re.sub(r"[^A-Z0-9]", "", r["text"].upper())

        if len(bare) < cfg.watermark_min_len:
            kept.append(r)
            continue

        if cfg.watermark_band is not None:
            lo, hi = cfg.watermark_band
            if not lo <= r["y"] <= hi:
                kept.append(r)
                continue

        if r.get("h", 0.0) / median_h < cfg.watermark_min_height_ratio:
            kept.append(r)
            continue

        score = max(fuzz.partial_ratio(bare, term) for term in cfg.watermark_terms)
        if score < cfg.watermark_min_similarity:
            kept.append(r)

    return kept


def run_ocr(crop: np.ndarray, engine: str | None = None,
            consensus: bool = False,
            min_width: int | None = None) -> list[dict]:
    """Read text from a rectified crop.

    Returns regions in crop-normalised coordinates, sorted top-to-bottom.
    Never raises on engine failure: a crop with no readable text and a crop
    whose engine crashed both produce an empty list, because no caller may
    depend on labels existing.
    """
    cfg = CFG.ocr
    img = _prepare(crop, min_width)

    names = [engine] if engine else (available_engines() if consensus
                                     else available_engines()[:1])
    if not names or names == [None]:
        return []

    per_engine = []
    for name in names:
        try:
            per_engine.append(_read_banded(get_engine(name), img))
        except Exception as exc:                       # noqa: BLE001
            print(f"  !! OCR engine {name} failed: {exc}")

    if not per_engine:
        return []

    if len(per_engine) > 1:
        regions = merge_engine_results(per_engine)
    else:
        regions = [dict(r, agree=1) for r in per_engine[0]]

    regions = [r for r in regions if r["conf"] >= cfg.min_conf]
    regions = drop_watermark(regions)
    regions.sort(key=lambda r: (round(r["y"], 3), r["x"]))
    return regions[:cfg.max_regions]
