"""Measure the query path against the catalog it was built from.

Every quality number this project reports -- recall@1 8/8, separation +0.077 --
comes from `match_eval.py`, which reads stored fingerprints and never uploads
an image. That measures the matcher. It does not measure the *query path*,
which extracts with `fast_ocr=True` where the catalog build does not, and
which is the only path a real user ever touches.

The difference is not cosmetic. `fast_ocr` reads fewer text regions, and text
regions drive `suppress_text_detections`, so a query can keep detections the
catalog build removed and arrive at a different button count for the same
photograph. Session 5 found one record doing exactly that (20 queried against
18 stored) purely by accident, because a decode bug had made it unqueryable
until then.

This uploads every catalog photograph to a running service and reports what
comes back:

    python scripts/query_drift.py --photos ../photos --fp ../work/fp

recall@1 here is the number that matters. It is allowed to be worse than
match_eval's; what is not allowed is nobody knowing by how much.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.match_eval import load_truth  # noqa: E402

BOUNDARY = "----rcudrift"
TRUTH_PATH = Path(__file__).resolve().parents[1] / "app/data/eval_truth.tsv"


def post_image(url: str, path: Path, token: str | None, timeout: float) -> dict:
    """Multipart POST without pulling in `requests`."""
    body = b"".join([
        f"--{BOUNDARY}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{path.name}"\r\n'.encode(),
        b"Content-Type: image/jpeg\r\n\r\n",
        path.read_bytes(),
        f"\r\n--{BOUNDARY}--\r\n".encode(),
    ])

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={BOUNDARY}")
    if token:
        req.add_header("X-Internal-Token", token)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}: {e.read().decode()[:120]}"}
    except Exception as e:                              # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


def records_for(stem: str, fps: dict[str, dict]) -> dict[str, dict]:
    """Catalog records extracted from one photograph.

    record_id is always `<photo stem>_<crop index>`, so the records belonging
    to a photo are its stem plus exactly one trailing integer. Matched by
    regex rather than `startswith`, because `ROLSEN_RSF-3106RT` is a prefix of
    `ROLSEN_RSF-3106RT_0`, which is a different photograph.
    """
    pattern = re.compile(re.escape(stem) + r"_\d+$")
    return {k: v for k, v in fps.items() if pattern.match(k)}


def load_truth(path: Path | None) -> dict[str, str]:
    """record_id -> product key, from `php artisan rcu:export-truth`."""
    if path is None:
        return {}
    out = {}
    for line in path.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] and parts[1]:
            out[parts[0]] = parts[1]
    return out


def correct_answers(stem: str, fps: dict[str, dict],
                    truth: dict[str, str]) -> set[str]:
    """Every record_id that would be a correct answer for this photograph.

    The photograph's own crops, plus every other record the catalogue says is
    the same product. Without the second half the measurement punishes the
    matcher for being right: the same remote is routinely catalogued twice
    under two filenames, and one physical remote is listed once per TV brand
    whose code set it carries. Ten of the thirteen "wrong" medium answers in
    the session-7 calibration were the first case alone, which is why medium
    read 78% when it was nearer 95%.

    Falls back to the stem rule when no truth file is given, so the evaluators
    still run without one -- but the number they print then is a floor, not a
    measurement.
    """
    mine = set(records_for(stem, fps))
    if not truth:
        return mine
    keys = {truth[r] for r in mine if r in truth}
    if not keys:
        return mine
    return mine | {r for r, k in truth.items() if k in keys and r in fps}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", type=Path, default=Path("../photos"))
    ap.add_argument("--fp", type=Path, default=Path("../work/fp"))
    ap.add_argument("--url", default="http://127.0.0.1:8600")
    ap.add_argument("--token", default=None)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--truth", default=str(TRUTH_PATH))
    args = ap.parse_args()

    fps = {p.stem: json.loads(p.read_text()) for p in sorted(args.fp.glob("*.json"))}
    if not fps:
        sys.exit(f"no fingerprints in {args.fp}")

    photos = sorted(p for ext in ("*.jpg", "*.jpeg", "*.png")
                    for p in args.photos.glob(ext))
    if not photos:
        sys.exit(f"no photos in {args.photos}")

    url = f"{args.url.rstrip('/')}/identify?top_k={args.top_k}"

    truth = load_truth(Path(args.truth)) if Path(args.truth).is_file() else {}

    hits = top5 = answerable = 0
    id_hits = id_answerable = 0
    errors: list[str] = []
    rows = []

    print(f"{'photo':34s} {'top-1 candidate':30s} {'conf':7s} "
          f"{'btn q/cat':>10s} {'score':>6s}  verdict")
    print("-" * 104)

    for photo in photos:
        mine = records_for(photo.stem, fps)
        result = post_image(url, photo, args.token, args.timeout)

        if "_error" in result:
            errors.append(f"{photo.name}: {result['_error']}")
            print(f"{photo.stem:34s} {'-':30s} {'ERROR':7s} {'-':>10s} {'-':>6s}  "
                  f"{result['_error']}")
            continue

        candidates = result.get("candidates") or []
        extracted = result.get("extracted") or {}
        top = candidates[0] if candidates else None
        top_id = top["record_id"] if top else None

        # The service matches the body with the most buttons, so the record it
        # ought to return is that photo's richest extraction.
        expected = max(mine, key=lambda k: mine[k]["stats"]["n_buttons"], default=None)
        stored = mine[expected]["stats"]["n_buttons"] if expected else None
        q_buttons = extracted.get("button_count")

        if expected is None:
            verdict = "no catalog record"
        else:
            answerable += 1
            ids = [c["record_id"] for c in candidates]
            if top_id in mine:
                hits += 1
                top5 += 1
                verdict = "HIT" if top_id == expected else f"hit (other crop: {top_id})"
            elif any(i in mine for i in ids):
                top5 += 1
                verdict = f"self at rank {min(i for i, x in enumerate(ids, 1) if x in mine)}"
            else:
                verdict = "self not retrieved"

            # Identity-level: did it name the right physical remote? Two
            # photographs of one remote are separate records, so returning the
            # other one is a correct answer to the question a user asked, and
            # scoring only self-retrieval understates the system. BAD records
            # are excluded exactly as match_eval excludes them.
            want = truth.get(expected)
            if want and want != "BAD":
                id_answerable += 1
                got = truth.get(top_id or "")
                if got == want:
                    id_hits += 1
                    if top_id not in mine:
                        verdict += " (same remote, other photo)"
                else:
                    verdict = "MISS: " + verdict

        drift = ""
        if stored is not None and q_buttons is not None and q_buttons != stored:
            drift = f"  [{q_buttons - stored:+d}]"

        print(f"{photo.stem:34s} {str(top_id):30s} {result.get('confidence','-'):7s} "
              f"{f'{q_buttons}/{stored}':>10s} "
              f"{top['score'] if top else 0:6.3f}  {verdict}{drift}")

        rows.append({
            "photo": photo.stem, "expected": expected, "top": top_id,
            "confidence": result.get("confidence"),
            "q_buttons": q_buttons, "cat_buttons": stored,
            "score": top["score"] if top else None,
            "latency_ms": result.get("latency_ms"),
        })

    print("-" * 104)
    if answerable:
        print(f"self-retrieval recall@1 {hits}/{answerable} "
              f"({100*hits/answerable:.0f}%)   recall@{args.top_k} {top5}/{answerable}")
    if id_answerable:
        # The number to quote. match_eval's recall is over stored
        # fingerprints; this one is over uploaded photographs, and only this
        # one describes what a user experiences.
        print(f"identity recall@1  {id_hits}/{id_answerable} "
              f"({100*id_hits/id_answerable:.0f}%)  [BAD records excluded, as in match_eval]")

    drifted = [r for r in rows
               if r["q_buttons"] is not None and r["cat_buttons"] is not None
               and r["q_buttons"] != r["cat_buttons"]]
    if drifted:
        deltas = [r["q_buttons"] - r["cat_buttons"] for r in drifted]
        print(f"button drift: {len(drifted)}/{len(rows)} records differ, "
              f"delta min {min(deltas):+d} max {max(deltas):+d}, "
              f"mean abs {sum(abs(d) for d in deltas)/len(deltas):.1f}")
    else:
        print("button drift: none")

    lat = [r["latency_ms"] for r in rows if r["latency_ms"]]
    if lat:
        print(f"latency ms: min {min(lat)} median {sorted(lat)[len(lat)//2]} max {max(lat)}")

    by_conf: dict[str, int] = {}
    for r in rows:
        by_conf[r["confidence"]] = by_conf.get(r["confidence"], 0) + 1
    print("confidence: " + ", ".join(f"{n} {c}" for c, n in sorted(by_conf.items())))

    if errors:
        print(f"\n{len(errors)} error(s):")
        for e in errors:
            print(f"  {e}")


if __name__ == "__main__":
    main()
