FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HOME=/tmp/huggingface XDG_CACHE_HOME=/tmp/cache
RUN useradd --create-home --uid 10001 docpipe
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libxcb1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu . \
    && python -m pip check
RUN python -c "from pathlib import Path; from docling.utils.model_downloader import download_models; download_models(output_dir=Path('/opt/docpipe/models'), rapidocr_models=['onnxruntime:eslav'], with_code_formula=False, with_picture_classifier=False, rapidocr_model_size='small')" \
    && chmod -R a+rX /opt/docpipe/models
COPY scripts/download_russian_recognition.py ./scripts/download_russian_recognition.py
RUN python scripts/download_russian_recognition.py /opt/docpipe/ru-rec \
    && python -c "from rapidocr import RapidOCR; from rapidocr.utils.typings import OCRVersion, ModelType, LangRec; [RapidOCR(params={'Det.ocr_version':OCRVersion.PPOCRV5,'Det.model_type':ModelType.MOBILE,'Rec.ocr_version':OCRVersion.PPOCRV5,'Rec.model_type':ModelType.MOBILE,'Rec.lang_type':lang}) for lang in (LangRec.ESLAV, LangRec.LATIN)]" \
    && chmod -R a+rX /opt/docpipe \
    && mkdir -p /app/data/jobs && chown -R docpipe:docpipe /app/data
ENV DOCPIPE_RU_REC_MODEL_DIR=/opt/docpipe/ru-rec DOCLING_ARTIFACTS_PATH=/opt/docpipe/models HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
USER docpipe
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz')"
CMD ["uvicorn", "docpipe.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "310"]
