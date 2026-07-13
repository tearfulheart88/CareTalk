FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    LANG=C.UTF-8 \
    MCP_HOST=0.0.0.0 \
    CARETALK_DB_PATH=/app/db/caretalk.db \
    MOCK_MODE=true \
    LIVE_API_ENABLED=false

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/db \
    && chown -R appuser:appuser /app

USER appuser
EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','9000')+'/health', timeout=3)" || exit 1

CMD ["python", "server.py"]
