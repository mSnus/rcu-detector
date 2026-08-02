FROM python:3.10-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=1

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
    && python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR()"

COPY app ./app
COPY scripts ./scripts

ENV RCU_SERVICE_HOST=0.0.0.0 \
    RCU_SERVICE_PORT=8600 \
    RCU_INDEX_PATH=/data/work/index/tokens.npz \
    RCU_FP_DIR=/data/work/fp

EXPOSE 8600

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8600/health || exit 1

CMD ["python", "-m", "app.main"]
