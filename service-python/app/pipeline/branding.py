"""Brand identification and model-code extraction from OCR text.

Both are bonus signals. A remote with neither still matches on geometry; a
remote with a confident model code skips straight to the fast path (plan 4.5).
That asymmetry is why precision matters far more than recall here -- a missing
code costs a little speed, a wrong code sends the match to the wrong remote
with high confidence.

Hence the `BUTTON_VOCAB` blocklist. It is not optional: without it a projector
remote labelled VGA1 / HDMI2 gets a model code of "VGA1", confidently.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from app.config import CFG

_BRANDS_FILE = Path(__file__).resolve().parents[1] / "data" / "brands.txt"

# Two letter groups may precede the digits: RM-D1110, RM-PJ20R. With a single
# group the prefix is silently dropped and RM-D1110 is stored as D1110, which
# is a different remote's code as far as the index is concerned.
#
# The trailing group is Samsung's whole scheme -- BN59-01315B, AA59-00543A --
# where a *second* digit group follows the separator. Without it the match
# stops at "BN59", which names a family of hundreds of remotes rather than one,
# and 642 of the 7431 catalogue titles carry a code in that shape.
#
# The tail requires a hyphen, never a space. With a space it swallows adjacent
# numerals: a keypad reading "VOL 12 34" becomes the code "VOL-12-34". The
# leading group keeps its space alternative because that is how "RM 530F" is
# printed, and there the digits are the last thing on the line.
MODEL_RE = re.compile(
    r"\b[A-Z]{1,5}(?:[- ][A-Z]{1,5})?[- ]?\d{2,6}[A-Z]{0,3}(?:-\d{2,6}[A-Z]{0,3})?\b"
)

# Text that looks exactly like a model code but is a button legend. Every
# entry here has been observed to be misread as a model code.
BUTTON_VOCAB = {
    "HDMI", "HDMI1", "HDMI2", "HDMI3", "HDMI4",
    "VGA", "VGA1", "VGA2", "AV", "AV1", "AV2", "AV3",
    "USB", "USB1", "USB2", "SCART", "YPBPR", "DVI", "RGB", "SVIDEO",
    "3D", "PIP", "SD", "HD", "UHD", "FHD", "CH", "VOL", "TV", "DVD",
    "VCR", "SAT", "AUX", "PS", "CLK", "OSD", "169", "43", "MPEG",
    "DTV", "ATV", "CATV", "DTT", "EPG", "MTS", "SAP", "CC",
    "P1", "P2", "P3", "F1", "F2", "F3", "F4", "A1", "A2", "B1", "B2",
    "MP3", "MP4", "AC3", "PAL", "NTSC", "SECAM", "MODE1", "MODE2",
    "SET1", "SET2", "TV1", "TV2", "DVD1", "STB1", "STB2",
}


# Words that appear on remote controls the world over. Used as an
# orientation signal: a crop that reads as English button legends is the right
# way up, and one that does not, is not.
LABEL_VOCAB = BUTTON_VOCAB | {
    "POWER", "MENU", "MUTE", "EXIT", "BACK", "HOME", "INFO", "GUIDE",
    "ENTER", "OK", "SOURCE", "INPUT", "SELECT", "SETUP", "SETTINGS",
    "VOLUME", "CHANNEL", "PLAY", "PAUSE", "STOP", "REC", "RECORD",
    "REW", "FWD", "NEXT", "PREV", "SKIP", "EJECT", "OPEN", "CLOSE",
    "ZOOM", "TEXT", "SUBTITLE", "AUDIO", "LANG", "REPEAT", "RANDOM",
    "SLEEP", "TIMER", "CLOCK", "LIST", "FAV", "FAVORITE", "RETURN",
    "STANDBY", "ASPECT", "PICTURE", "SOUND", "BASS", "TREBLE", "ECHO",
    "LIGHT", "MODE", "SEARCH", "SCAN", "STORE", "MOVIE", "MUSIC",
    "PHOTO", "APPS", "NETFLIX", "YOUTUBE", "SMART", "GAME", "FREEZE",
    "KEYSTONE", "BLANK", "FOCUS", "LAMP", "PATTERN", "COMPUTER",
    "REMOTE", "CONTROL", "CONTROLLER", "UNIVERSAL",
}


def vocab_hits(regions: list[dict]) -> int:
    """How many text regions read as recognisable remote-control words."""
    n = 0
    for r in regions:
        for token in r["text"].split():
            bare = re.sub(r"[^A-Z0-9]", "", token)
            if len(bare) >= 2 and bare in LABEL_VOCAB:
                n += 1
                break
    return n


@lru_cache(maxsize=1)
def known_brands() -> list[str]:
    """Brand list from app/data/brands.txt, comments stripped."""
    if not _BRANDS_FILE.exists():
        return []
    out = []
    for line in _BRANDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


@lru_cache(maxsize=1)
def _brand_lookup() -> dict[str, str]:
    """Upper-cased, punctuation-free form -> canonical brand name."""
    return {re.sub(r"[^A-Z0-9]", "", b.upper()): b for b in known_brands()}


def _fuzzy_match(token: str, threshold: int) -> tuple[str, float] | None:
    """Best brand for a token, or None. Uses rapidfuzz when installed.

    Exact hits are always accepted. Fuzzy hits are only considered for tokens
    of at least `fuzzy_min_len`: on a short word one edit barely dents the
    similarity score, so button legends land on short brand names (SPACE ->
    Pace, IRIS -> Irbis, both 88.9 against a threshold of 85).
    """
    lookup = _brand_lookup()
    key = re.sub(r"[^A-Z0-9]", "", token.upper())
    if len(key) < 3:
        return None
    if key in lookup:
        return lookup[key], 100.0
    if len(key) < CFG.brand.fuzzy_min_len:
        return None
    try:
        from rapidfuzz import process, fuzz
        hit = process.extractOne(key, lookup.keys(), scorer=fuzz.ratio,
                                 score_cutoff=threshold)
        if hit:
            return lookup[hit[0]], float(hit[1])
    except ImportError:
        # difflib fallback keeps this module importable without rapidfuzz
        import difflib
        close = difflib.get_close_matches(key, list(lookup), n=1,
                                          cutoff=threshold / 100.0)
        if close:
            score = difflib.SequenceMatcher(None, key, close[0]).ratio() * 100
            return lookup[close[0]], float(score)
    return None


def _alpha_len(text: str) -> int:
    return sum(1 for c in text if c.isalpha())


def _is_button_legend(text: str) -> bool:
    """True if this text is a word printed on remotes rather than a brand.

    Checked against the whole string and each token, so both "MENU" and
    "PICTURE MODE" are caught. A brand that genuinely collides with the
    vocabulary would be rejected here, but the verified list path runs first
    and this only guards the unverified fallback.
    """
    bare = re.sub(r"[^A-Z0-9]", "", text.upper())
    if bare in LABEL_VOCAB:
        return True
    return any(re.sub(r"[^A-Z0-9]", "", t.upper()) in LABEL_VOCAB
               for t in text.split())


def find_brand(regions: list[dict]) -> dict | None:
    """Identify the brand wordmark among the text regions.

    Ordered strategy, per plan 3.8:
      1. fuzzy-match every token against the known-brand list  (verified)
      2. otherwise the largest isolated text is a candidate     (unverified)
      3. visual wordmark templates -- not implemented; stylised logotypes
         defeat OCR often enough that this is the main future gain here

    Returns {"name", "conf", "source"} or None.
    """
    cfg = CFG.brand
    if not regions:
        return None

    best: tuple[float, dict] | None = None
    for r in regions:
        for token in r["text"].split():
            hit = _fuzzy_match(token, cfg.fuzzy_threshold)
            if not hit:
                continue
            name, score = hit
            # brands are usually printed large and near an end of the body
            bonus = 1.0 + (0.15 if r["y"] < 0.18 or r["y"] > 0.86 else 0.0)
            ranked = score * bonus * (0.5 + 0.5 * r["conf"])
            if best is None or ranked > best[0]:
                best = (ranked, {"name": name, "conf": round(r["conf"], 3),
                                 "source": "list", "score": round(score, 1)})
    if best:
        return best[1]

    # Nothing matched: the largest *isolated* text is a candidate wordmark.
    #
    # Both guards below are load-bearing, and the size test alone is not
    # enough. `median_h` is a statistic over however much text the OCR
    # happened to read, so it moves between the catalog path and the query
    # path: on SMART_TV_T96 the query reads 18 regions to the catalog's 25,
    # the median drops, and the APP button legend clears the threshold and is
    # stored as the brand "App" -- on the query side only. A false brand is
    # worse than no brand, because `brand_agreement` believes it.
    #
    # Isolation is the discriminator the size test is missing: a wordmark sits
    # in clear space at an end of the body, while APP has MEDIA, TV and WEB
    # ranged alongside it. The vocabulary blocklist is the same guard
    # `find_model_code` already applies for VGA1 / HDMI2 -- a word that is a
    # known button legend is never a brand.
    if not cfg.unverified_wordmark:
        return None

    heights = sorted(r["h"] for r in regions)
    median_h = heights[len(heights) // 2]
    candidates = [r for r in regions
                  if r["h"] >= cfg.big_text_ratio * median_h
                  and not _has_digit(r["text"])
                  and _alpha_len(r["text"]) >= cfg.min_wordmark_alpha
                  and not _is_button_legend(r["text"])
                  and _is_isolated(r, regions, cfg.wordmark_isolation_radius)]
    if candidates:
        r = max(candidates, key=lambda r: r["h"])
        return {"name": r["text"].title(), "conf": round(r["conf"] * 0.5, 3),
                "source": "unverified"}
    return None


def _has_digit(text: str) -> bool:
    return any(c.isdigit() for c in text)


def _is_isolated(region: dict, regions: list[dict], radius: float) -> bool:
    for other in regions:
        if other is region:
            continue
        dx = other["x"] - region["x"]
        dy = other["y"] - region["y"]
        if (dx * dx + dy * dy) ** 0.5 < radius:
            return False
    return True


def _button_within(region: dict, buttons: list[dict], radius: float) -> bool:
    for b in buttons:
        dx = b["x"] - region["x"]
        dy = b["y"] - region["y"]
        if (dx * dx + dy * dy) ** 0.5 < radius:
            return True
    return False


def find_model_code(regions: list[dict], buttons: list[dict],
                    brand: dict | None = None) -> str | None:
    """Extract the printed model code, or None.

    Filters, in the order they earn their keep:
      - the BUTTON_VOCAB blocklist (VGA1, HDMI2, AV2)
      - must mix letters and digits, and be long enough to be distinctive
      - must not sit inside the keypad -- that makes it a button legend
      - the brand wordmark itself is not a code
    """
    cfg = CFG.brand
    candidates: list[tuple[float, str]] = []

    for r in regions:
        text = r["text"]
        for m in MODEL_RE.finditer(text):
            tok = m.group()
            bare = tok.replace("-", "").replace(" ", "")
            if bare in BUTTON_VOCAB:
                continue
            if len(bare) < cfg.model_min_len:
                continue
            if not (any(c.isdigit() for c in bare) and any(c.isalpha() for c in bare)):
                continue
            if brand and bare == re.sub(r"[^A-Z0-9]", "", brand["name"].upper()):
                continue

            score = 1.0 + r["conf"]
            if r["y"] > cfg.model_bottom_y:
                score += 0.5              # codes live near the bottom
            if _is_isolated(r, regions, cfg.isolation_radius):
                score += 0.5
            if "-" in tok:
                # A separator is the strongest single signal that a token is a
                # code: RM-530F, RC-371M, MR-18B, RSF-3106RT. OCR garbage that
                # satisfies the regex ("LIVE ZOOM" read as LIVE20OM) almost
                # never carries one.
                score += cfg.separator_bonus
            # Sitting near a button is evidence of a legend, not proof of one:
            # a code printed just below the keypad is within the radius, and
            # excluding it outright handed MR-18B's slot to LIVE20OM. Penalise
            # instead, and let the separator and position decide.
            if _button_within(r, buttons, cfg.button_radius):
                score -= cfg.button_penalty
            if r.get("agree", 1) > 1:
                score += 0.25            # engines agreed on the reading
            candidates.append((score, tok.replace(" ", "-")))

    if not candidates:
        return None
    return max(candidates, key=lambda c: c[0])[1]
