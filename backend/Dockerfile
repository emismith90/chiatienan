FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_URL=sqlite:////data/chiatienan.db \
    DATA_DIR=/data

WORKDIR /app

# Node runs the agent sidecar (agent_sidecar/main.js). >=22.19 is what its
# package.json requires; NodeSource is used because Debian slim ships an older one.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && node --version \
    && apt-get purge -y gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY kernos ./kernos
COPY ledger_core ./ledger_core
COPY packs ./packs
RUN pip install .

# The sidecar, installed from the committed lockfile so a deploy is reproducible.
# --omit=dev because node --test needs no extra packages; --ignore-scripts because
# nothing here has a legitimate postinstall.
COPY agent_sidecar ./agent_sidecar
RUN cd agent_sidecar && npm ci --omit=dev --ignore-scripts

# Curated place/observation seeds, so `python -m app.seed_places` runs inside the
# container as the runbook documents. They are versioned data, not config: the
# loader is idempotent precisely because re-seeding after adding aliases is an
# expected recurring operation, and that is impossible if the files only exist in
# the repo. Text-only and small (~40 KB).
COPY seeds ./seeds

RUN mkdir -p /data
EXPOSE 8000

# MUST stay a single worker/process: app.chat._agent_lock and app.realtime.hub
# are in-process state (asyncio.Lock + in-memory pub/sub queues) and assume
# one process owns the whole app. Do not add --workers or run multiple
# replicas behind the same DB without redesigning those.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
