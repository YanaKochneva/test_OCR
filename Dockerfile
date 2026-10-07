FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 docpipe
WORKDIR /app
COPY pyproject.toml constraints.txt ./
COPY src ./src
COPY eval ./eval
COPY docs ./docs
COPY scripts ./scripts
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -e .
USER docpipe
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "docpipe.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
