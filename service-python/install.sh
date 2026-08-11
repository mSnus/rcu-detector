#!/usr/bin/env bash
#
# Set up the Python CV service environment from a bare Debian/Ubuntu box.
#
#   cd service-python && ./install.sh
#
# Idempotent: safe to re-run. Re-running after editing requirements.txt is the
# supported way to add a dependency.
#
# Notes on why this is not just "python3 -m venv .venv":
#   - The target box ships Python 3.10 without ensurepip (no python3-venv
#     package, and no root to apt-install it), so the venv is created with
#     `virtualenv`, which bundles its own pip.
#   - PaddleOCR is the engine the implementation plan specifies, but
#     paddlepaddle + paddleocr need ~1 GB of disk and more RAM than a 2 GB box
#     has. RapidOCR runs the same PP-OCR models under onnxruntime in ~150 MB,
#     so it is the default here. See OCR_EXTRA below to install the others.

set -euo pipefail

cd "$(dirname "$0")"

VENV="${VENV:-.venv}"
PY="${PYTHON:-python3}"
# Optional extra OCR engines: OCR_EXTRA=paddle|easyocr|doctr|tesseract ./install.sh
OCR_EXTRA="${OCR_EXTRA:-}"

say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }

say "Python: $($PY --version 2>&1)"

# --- 1. virtual environment -------------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
    if "$PY" -c 'import ensurepip' 2>/dev/null; then
        say "Creating venv with the stdlib venv module"
        "$PY" -m venv "$VENV"
    else
        say "ensurepip missing -> bootstrapping virtualenv into the user site"
        "$PY" -m pip install --user -q virtualenv
        "$PY" -m virtualenv -q "$VENV"
    fi
else
    say "Reusing existing venv at $VENV"
fi

PIP="$VENV/bin/pip"
VPY="$VENV/bin/python"

# --- 2. dependencies --------------------------------------------------------
say "Installing requirements.txt"
"$PIP" install -q --upgrade pip
"$PIP" install -q -r requirements.txt

# The OCR wrappers are installed separately and WITHOUT their dependency
# metadata, which is why they are not in requirements.txt: pip resolves
# everything named in a requirements file, so listing them there constrains
# the pins even though this step installs them --no-deps. Both declare the
# full opencv-python, and one declares numpy<2.0.0. See requirements-ocr.txt.
say "Installing the OCR wrappers (--no-deps, deliberately)"
"$PIP" install -q --no-deps -r requirements-ocr.txt

# Both OCR packages depend on the full opencv build, which installs over the
# headless one under the same `cv2` module name -- whichever lands last wins.
# Force headless back on top: the OpenCV version changes every fingerprint.
say "Pinning opencv back to the headless build"
"$PIP" uninstall -y -q opencv-python >/dev/null 2>&1 || true
"$PIP" install -q --force-reinstall --no-deps opencv-python-headless==4.10.0.84

# Assert it, rather than trusting the ordering above. --no-deps means nothing
# else will notice if this drifts.
"$VPY" -c "import cv2, numpy; \
assert cv2.__version__.startswith('4.10.'), 'wrong OpenCV: ' + cv2.__version__; \
print('  OpenCV', cv2.__version__, '/ numpy', numpy.__version__)"

case "$OCR_EXTRA" in
    "")        ;;
    paddle)    say "Installing PaddleOCR (~1 GB)"
               "$PIP" install -q paddlepaddle==2.6.1 paddleocr==2.8.1 ;;
    easyocr)   say "Installing EasyOCR (pulls torch, ~2.5 GB)"
               "$PIP" install -q easyocr==1.7.2 ;;
    doctr)     say "Installing docTR"
               "$PIP" install -q "python-doctr[torch]==0.9.0" ;;
    tesseract) say "Installing pytesseract (needs the tesseract binary via apt)"
               "$PIP" install -q pytesseract==0.3.13 ;;
    *)         echo "unknown OCR_EXTRA: $OCR_EXTRA" >&2; exit 2 ;;
esac

# --- 3. warm the OCR model cache -------------------------------------------
# The recognition weights download on first use. Pull them now rather than on
# the first user request.
say "Warming the OCR model cache"
"$VPY" - <<'PY'
import sys
sys.path.insert(0, ".")
from app.pipeline.ocr import get_engine, available_engines
print("engines available:", ", ".join(available_engines()) or "none")
eng = get_engine()
print("warmed:", eng.name)
PY

# --- 4. smoke test ----------------------------------------------------------
say "Smoke test"
"$VPY" - <<'PY'
import cv2, numpy, rapidfuzz
print("cv2", cv2.__version__, "| numpy", numpy.__version__,
      "| rapidfuzz", rapidfuzz.__version__)
PY

say "Done. Activate with:  source $VENV/bin/activate"
