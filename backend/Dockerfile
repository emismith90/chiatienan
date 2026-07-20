FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_URL=sqlite:////data/chiatienan.db \
    CURSOR_SDK_WORKSPACE=/data/cursor-agent

WORKDIR /app

# git + curl: the cursor-sdk bridge subprocess expects a normal toolchain in its
# workspace (design §8 — a plain, non-gVisor container).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
RUN pip install .

RUN mkdir -p /data
EXPOSE 8000

# MUST stay a single worker/process: app.chat._agent_lock and app.realtime.hub
# are in-process state (asyncio.Lock + in-memory pub/sub queues) and assume
# one process owns the whole app. Do not add --workers or run multiple
# replicas behind the same DB without redesigning those.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
