FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    LANG=C.UTF-8

WORKDIR /app
COPY . /src

RUN set -eux; \
    if [ -f /src/server.py ] && [ -f /src/requirements.txt ]; then \
      cp -a /src /app/caretalk; \
    elif [ -f /src/workspace/projects/caretalk_돌봄톡/server.py ]; then \
      cp -a /src/workspace/projects/caretalk_돌봄톡 /app/caretalk; \
    else \
      echo "CareTalk source directory was not found."; \
      exit 1; \
    fi; \
    pip install --no-cache-dir --upgrade pip; \
    pip install --no-cache-dir -r /app/caretalk/requirements.txt; \
    mkdir -p /app/caretalk/db

WORKDIR /app/caretalk
EXPOSE 9000

CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "9000"]
