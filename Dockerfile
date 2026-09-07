# Live WS ingester (QNT-456, FR-1). Runs only during demo sessions on Fargate.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv \
    && uv export --no-emit-project --no-default-groups --format requirements.txt --quiet -o requirements.txt \
    && pip install --no-cache-dir -r requirements.txt \
    && rm requirements.txt

COPY src/hyperlake ./src/hyperlake
COPY config/watchlist.yaml ./config/watchlist.yaml

# `hyperlake.watchlist.load_watchlist()`'s default path is `parents[2]` of this file
# (src-layout checkout) -- WORKDIR here plays the role of the repo root, so the src/
# layout must be preserved rather than flattened.
ENV PYTHONPATH=/app/src

RUN useradd --system --create-home ingester
USER ingester

ENTRYPOINT ["python", "-m", "hyperlake.ingester"]
