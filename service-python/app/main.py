"""FastAPI recognition service (plan section 5).

Bound to 127.0.0.1:8600 and called by Laravel over internal HTTP. All computer
vision lives here; none of it belongs in PHP.

  GET  /health            readiness and index size
  POST /identify          image -> ranked candidates
  POST /fingerprint       image -> raw fingerprint (debug/admin)
  POST /reindex           rebuild the in-memory index
  GET  /debug/{id}        annotated overlay for a recent request

The index is loaded once into module state at startup, never per request.
"""
from __future__ import annotations

import os
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from app.config import CFG
from app.matching.index import TokenIndex
from app.matching.matcher import Matcher
from app.matching.store import JsonDirStore
from app.pipeline.extract import ExtractedRemote, debug_panel, extract_remotes
from app.pipeline.imageio import decode_image
from app.pipeline.ocr import available_engines

class State:
    """Module-level service state. Populated once, on startup."""

    def __init__(self) -> None:
        self.matcher: Matcher | None = None
        self.ready = False
        self.error: str | None = None
        self.ocr_engines: list[str] = []
        # Recent overlays for GET /debug/{request_id}. Bounded, because this
        # box runs out of memory long before it runs out of anything else.
        self.debug: OrderedDict[str, bytes] = OrderedDict()

    def remember_debug(self, request_id: str, jpeg: bytes) -> None:
        self.debug[request_id] = jpeg
        while len(self.debug) > CFG.service.debug_cache_size:
            self.debug.popitem(last=False)


STATE = State()


def _index_path() -> Path:
    return Path(os.environ.get("RCU_INDEX_PATH", CFG.service.index_path))


def _fp_dir() -> Path:
    return Path(os.environ.get("RCU_FP_DIR",
                               str(_index_path().parent / "fp")))


def load_index() -> None:
    """(Re)load the index and fingerprint store into module state."""
    try:
        store = JsonDirStore(_fp_dir())
        index = TokenIndex.load(_index_path())
        STATE.matcher = Matcher(index, store)
        STATE.ready = True
        STATE.error = None
    except Exception as exc:                             # noqa: BLE001
        # Start anyway and report unready. Laravel can then fail fast with a
        # clean message instead of every upload timing out against a service
        # that never finished booting.
        STATE.matcher = None
        STATE.ready = False
        STATE.error = f"{type(exc).__name__}: {exc}"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    load_index()
    STATE.ocr_engines = available_engines()
    if not os.environ.get(CFG.service.auth_env):
        print(f"WARNING: {CFG.service.auth_env} is unset -- "
              f"{CFG.service.auth_header} will not be checked. Acceptable "
              f"only because the service is bound to {CFG.service.host}.")
    yield


app = FastAPI(title="RCU Identifier", version="0.3.0", lifespan=lifespan)


def require_token(x_internal_token: str | None = Header(default=None)) -> None:
    """Shared-secret check (plan 5.4).

    Combined with binding to loopback this is sufficient. When the secret is
    unset the check is skipped, so a dev box works out of the box; startup
    logs a warning so that is a visible choice rather than a silent hole.
    """
    expected = os.environ.get(CFG.service.auth_env)
    if not expected:
        return
    if x_internal_token != expected:
        raise HTTPException(status_code=401, detail="bad internal token")


async def _read_image(file: UploadFile) -> np.ndarray:
    """Decode an upload exactly as the catalog build decodes a file.

    Both go through `app.pipeline.imageio`, which is the whole point of that
    module: a truncated JPEG otherwise decodes to a partly-uninitialised
    buffer whose contents differ between the two paths and between successive
    calls. See imageio's docstring -- it cost a query recall of 14/18 before
    anyone measured it.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(raw) > CFG.service.max_upload_bytes:
        raise HTTPException(status_code=413, detail="image too large")
    img = decode_image(raw)
    if img is None:
        raise HTTPException(status_code=400, detail="undecodable image")
    return img


def _extracted_summary(r: ExtractedRemote) -> dict:
    fp = r.fingerprint
    return {
        "brand": fp.get("brand"),
        "model_code": fp.get("model_code"),
        "button_count": fp["stats"]["n_buttons"],
        "quality": fp["extract_quality"],
        "orientation_conf": fp["stats"]["orientation_conf"],
    }


@app.get("/health")
def health() -> dict:
    m = STATE.matcher
    return {
        "status": "ok" if STATE.ready else "unavailable",
        "index_docs": m.index.n_docs if m else 0,
        "index_records": m.index.n_records if m else 0,
        "catalog_fingerprints": len(m.store) if m else 0,
        "ocr_engines": STATE.ocr_engines,
        "version": app.version,
        "error": STATE.error,
    }


@app.post("/identify", dependencies=[Depends(require_token)])
async def identify(image: UploadFile = File(...), top_k: int = 5,
                   debug: bool = False) -> JSONResponse:
    if not STATE.ready or STATE.matcher is None:
        raise HTTPException(status_code=503,
                            detail=f"index not loaded: {STATE.error}")

    t0 = time.time()
    request_id = uuid.uuid4().hex[:16]
    img = await _read_image(image)

    # The ensemble stays ON here, though this is the query path. Measured, it
    # is nearly free -- most images 0.0-0.1 s, worst case 1.6 s on a 4000px
    # one -- because the cost is thresholding, not the OCR that dominates.
    # Single-pass uses one block size and that is a whole failure class: the
    # HONEYWELL HE5500's grey-on-grey buttons yield nothing at 0.075, so the
    # query returned 0 buttons and "reshoot" on a remote the catalog holds
    # correctly. Latency is worth trading for that; button recall is not.
    #
    # `fast_ocr` is where the query economy lives instead: one OCR pass at a
    # lower upscale, trading small-legend recall and the text orientation
    # signal for ~14 s.
    #
    # Giving up the text orientation signal is only safe because the matcher
    # now tries *every* query both ways up (CFG.index.trust_query_orientation).
    # It did not always: it applied the index side's confidence threshold to
    # the query, and geometry-only orientation is confidently wrong often
    # enough that RM-PJ20_big_light was never retrieved at all and
    # Huayu_Motorola_Cisco_15 scored 0.488 instead of 0.901. The economy and
    # the both-ways retrieval are a pair; do not keep one without the other.
    remotes = extract_remotes(img, ensemble=True, fast_ocr=True)
    if not remotes:
        return JSONResponse({
            "request_id": request_id, "confidence": "none",
            "latency_ms": int((time.time() - t0) * 1000),
            "extracted": None, "candidates": [],
            "hint": "reshoot",
            "message": "no remote found in the image",
        })

    # Largest body first; a query photo should hold one remote, and if it
    # holds more the biggest is the one being photographed.
    remote = max(remotes, key=lambda r: r.fingerprint["stats"]["n_buttons"])
    result = STATE.matcher.match(remote.fingerprint, top_k=top_k)

    if debug:
        ok, buf = cv2.imencode(".jpg", debug_panel(img, remote),
                               [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok:
            STATE.remember_debug(request_id, buf.tobytes())

    latency = int((time.time() - t0) * 1000)
    if latency > CFG.service.target_latency_ms:
        # Logged, not enforced. The query path has a ~1s budget; exceeding it
        # is worth knowing about but never worth failing a request over.
        print(f"slow query {request_id}: {latency} ms")

    return JSONResponse({
        "request_id": request_id,
        "confidence": result.confidence,
        "latency_ms": latency,
        "extracted": _extracted_summary(remote),
        "candidates": [c.as_dict() for c in result.candidates],
        "hint": result.hint,
        "bodies_found": len(remotes),
        "retrieved": result.n_retrieved,
        "model_code_fast_path": result.fast_path,
        "debug_url": f"/debug/{request_id}" if debug else None,
    })


@app.post("/fingerprint", dependencies=[Depends(require_token)])
async def fingerprint(image: UploadFile = File(...),
                      ensemble: bool = True, ocr: bool = True) -> JSONResponse:
    """Raw extraction, no matching. For the admin visualiser and for triage.

    Defaults to the offline build's settings rather than the query path's, so
    what comes back is what the catalog would have stored for this image.
    """
    img = await _read_image(image)
    remotes = extract_remotes(img, ensemble=ensemble, use_ocr=ocr)
    return JSONResponse({
        "bodies_found": len(remotes),
        "fingerprints": [r.fingerprint for r in remotes],
    })


@app.post("/reindex", dependencies=[Depends(require_token)])
def reindex() -> dict:
    """Reload the index from disk after a catalog rebuild."""
    load_index()
    if not STATE.ready:
        raise HTTPException(status_code=500, detail=STATE.error)
    m = STATE.matcher
    assert m is not None
    return {"status": "ok", "index_docs": m.index.n_docs,
            "index_records": m.index.n_records}


@app.get("/debug/{request_id}")
def debug_image(request_id: str) -> Response:
    jpeg = STATE.debug.get(request_id)
    if jpeg is None:
        raise HTTPException(status_code=404,
                            detail="no overlay for that request id")
    return Response(content=jpeg, media_type="image/jpeg")


def main() -> None:
    import uvicorn
    uvicorn.run(app, host=CFG.service.host, port=CFG.service.port,
                log_level="info")


if __name__ == "__main__":
    main()
