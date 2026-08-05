FROM python:3.10-slim-bookworm

# HOME is /tmp rather than a user's home directory on purpose: this image is
# meant to run under whatever uid the host needs (see `user:` in
# docker-compose.yml), and that uid will not own /home/anything. Anything that
# wants a home directory gets a writable one regardless of who is running.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=1 \
    HOME=/tmp

# opencv-python-headless still needs libgl/libglib at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/service-python

COPY requirements.txt ./
# rapidocr-onnxruntime pulls opencv-python (needs libGL) and would shadow the
# headless build pinned in requirements.txt, so it is installed without deps.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --no-deps rapidocr-onnxruntime==1.3.24 \
    && pip install --no-cache-dir --no-deps rapidocr-openvino==1.4.4 \
    # Both OCR packages depend on the full opencv build. Whichever landed last
    # wins under the same `cv2` name, so put headless back on top explicitly --
    # install.sh does the same, and the Dockerfile was relying on ordering.
    && pip install --no-cache-dir --force-reinstall --no-deps \
         opencv-python-headless==4.10.0.84 \
    && python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR()" \
    && python -c "from rapidocr_openvino import RapidOCR; RapidOCR()"

COPY app ./app
COPY scripts ./scripts

ENV RCU_SERVICE_HOST=0.0.0.0 \
    RCU_SERVICE_PORT=8600 \
    RCU_INDEX_PATH=/data/work/index/tokens.npz \
    RCU_FP_DIR=/data/work/fp

EXPOSE 8600

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8600/health || exit 1

# Never root. The build writes fingerprints, overlays and the token index into
# a bind-mounted host directory, and root-owned build artefacts are a nuisance
# the host cannot clear without another container.
#
# 1000 is only a default; nothing here depends on it. Compose overrides it with
# the uid that owns work/ on the host -- see RCU_UID in .env.example. The
# models and code are world-readable, so any uid works.
RUN useradd --create-home --uid 1000 rcu
USER rcu

CMD ["python", "-m", "app.main"]
