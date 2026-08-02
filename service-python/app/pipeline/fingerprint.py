"""Assemble the fingerprint document from pipeline outputs.

The fingerprint is the only thing that gets stored and matched. Keep it
JSON-serialisable, versioned, and free of pixel data.
"""
from __future__ import annotations

import numpy as np

from app.config import CFG
from app.pipeline.color import CHROMATIC


def extraction_quality(buttons: list[dict], aspect: float,
                       orient_conf: float) -> float:
    """0..1 confidence that this extraction is usable.

    Drives the review queue (worst 5% get looked at) and the decision to fall
    back to the embedding channel at match time.

    Label recall contributes, but weakly and with a low bar: a black remote
    photographed under tungsten light yields almost no labels and is still a
    perfectly matchable extraction. Weighting labels heavily here would push
    exactly the hard-but-valid cases into the review queue.
    """
    cfg = CFG.quality
    n = len(buttons)

    if n < cfg.min_plausible_buttons or n > cfg.max_plausible_buttons:
        count_score = 0.2
    elif cfg.ideal_button_range[0] <= n <= cfg.ideal_button_range[1]:
        count_score = 1.0
    else:
        count_score = 0.65

    # remotes are elongated; a near-square body usually means bad detection
    aspect_score = 1.0 if 2.0 <= aspect <= 6.5 else 0.5

    # consistent colour readings suggest clean lighting
    if buttons:
        conf = float(np.mean([b.get("color_conf", 0.5) for b in buttons]))
    else:
        conf = 0.0

    # detections agreed on by several threshold passes are more trustworthy
    if buttons:
        vote_score = float(np.mean([min(b.get("votes", 1), 3) / 3 for b in buttons]))
    else:
        vote_score = 0.0

    labelled = sum(1 for b in buttons if b.get("label"))
    recall = labelled / n if n else 0.0
    label_score = min(recall / max(cfg.good_label_recall, 1e-6), 1.0)

    return round(float(
        0.32 * count_score
        + 0.13 * aspect_score
        + 0.22 * conf
        + 0.13 * vote_score
        + 0.10 * orient_conf
        + 0.10 * label_score
    ), 3)


def build_fingerprint(buttons: list[dict], aspect: float,
                      orientation: dict,
                      text_regions: list[dict] | None = None,
                      brand: dict | None = None,
                      model_code: str | None = None,
                      body: dict | None = None) -> dict:
    """Produce the stored fingerprint document."""
    clean = []
    for b in buttons:
        clean.append({
            "x": round(float(b["x"]), 4),
            "y": round(float(b["y"]), 4),
            "w": round(float(b["w"]), 4),
            "h": round(float(b["h"]), 4),
            "shape": b.get("shape", "unknown"),
            "color": b.get("color", "grey"),
            "label": b.get("label"),
            "label_pos": b.get("label_pos"),
            "conf": round(float(b.get("color_conf", 0.5)), 3),
        })

    # Only captions are stored. Region text already assigned to a button
    # lives on that button, and storing it twice would let it score twice.
    captions = []
    for r in text_regions or []:
        if r.get("role") == "label":
            continue
        captions.append({
            "x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"],
            "size": r["h"],            # plan 2.2 calls this `size`
            "text": r["text"],
            "conf": r["conf"],
        })

    chromatic = [b for b in clean if b["color"] in CHROMATIC]
    labelled = [b for b in clean if b["label"]]

    return {
        "v": CFG.fingerprint_version,
        # How the body was found is kept so the audit can rank review by it:
        # a full-frame fallback or a split blob is a weaker starting point than
        # a cleanly segmented silhouette, whatever the button count says.
        "body": {
            "aspect": round(float(aspect), 3),
            "shape": "rounded_rect",
            "area_frac": round(float(body["area_frac"]), 4) if body else None,
            "full_frame": bool(body.get("full_frame")) if body else False,
            "split": bool(body.get("split")) if body else False,
        },
        "buttons": clean,
        "text_regions": captions,
        "brand": brand["name"] if brand else None,
        "brand_source": brand["source"] if brand else None,
        "brand_conf": brand["conf"] if brand else None,
        "model_code": model_code,
        "stats": {
            "n_buttons": len(clean),
            "n_chromatic": len(chromatic),
            "n_labelled": len(labelled),
            "n_captions": len(captions),
            "label_recall": round(len(labelled) / len(clean), 3) if clean else 0.0,
            "orientation_flipped": bool(orientation.get("flip")),
            "orientation_conf": round(float(orientation.get("confidence", 0)), 3),
        },
        "extract_quality": extraction_quality(
            buttons, aspect, float(orientation.get("confidence", 0))),
    }
