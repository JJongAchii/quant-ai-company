# Build targets: app, autonomous-research, maintenance, codex, claude and video. Release builds require the committed
# uv.lock. Every layer up to the dependencies and tools of a target is independent of the release commit, so a code-only
# release reuses them from the build cache and rebuilds only the source, project and label layers (docs/adr/0045).
FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS codex-dist
COPY src/quant_company/providers/codex_release.json /tmp/codex_release.json
COPY deploy/install-codex.cjs /tmp/install-codex.cjs
RUN node /tmp/install-codex.cjs /tmp/codex_release.json

# This qualified native distribution targets the existing Linux x86_64 office host.
FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS claude-dist
RUN npm install --global --omit=dev @anthropic-ai/claude-code@2.1.280 \
    && install -m 0755 "$(readlink -f /usr/local/bin/claude)" /claude \
    && echo '1e08503dbdf3c2cb0d706d32f3408277388d1c76ef108673e8fe42c1b322925b  /claude' | sha256sum -c - \
    && /claude --version | grep -Fx '2.1.280 (Claude Code)'

FROM ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa AS uv-dist

# Python, the locked dependencies and the quant-data package; no company source yet.
FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH=/app/.venv/bin:$PATH
COPY --from=uv-dist /uv /usr/local/bin/uv
# Preserve the sibling qdata path recorded in uv.lock.
WORKDIR /opt/company
RUN ln -s /opt/company /app \
    && groupadd --gid 10001 company \
    && useradd --uid 10001 --gid 10001 --create-home company
COPY --from=qdata pyproject.toml README.md /opt/quant-data/
COPY --from=qdata src /opt/quant-data/src
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable --no-install-project --extra lake
ENTRYPOINT ["python", "/app/entrypoint.py"]

# Each target below ends with the same release layers: the company source installed into the environment, the entry
# point, then the commit labels (ARG values reach only these last instructions, so they never invalidate the cache).
FROM base AS app
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable --extra lake
COPY deploy/entrypoint.py /opt/company/entrypoint.py
ARG RELEASE_COMMIT=unqualified
ARG QDATA_COMMIT=unqualified
LABEL org.opencontainers.image.revision=$RELEASE_COMMIT org.quant-company.qdata-revision=$QDATA_COMMIT
ENV QDATA_CODE_COMMIT=$QDATA_COMMIT COMPANY_CODE_COMMIT=$RELEASE_COMMIT
USER 10001:10001
CMD ["quant-company", "serve", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS autonomous-research-tools
# Trusted Git operations build scoped research bundles and verify the pinned qlab checkout.
# Candidate code executes only in the 3070 sandbox, never in this image.
RUN apt-get update && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && /app/.venv/bin/python -c "import yaml; assert yaml.__version__ == '6.0.3'"

FROM autonomous-research-tools AS autonomous-research
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable --extra lake
COPY deploy/entrypoint.py /opt/company/entrypoint.py
ARG RELEASE_COMMIT=unqualified
ARG QDATA_COMMIT=unqualified
LABEL org.opencontainers.image.revision=$RELEASE_COMMIT org.quant-company.qdata-revision=$QDATA_COMMIT
ENV QDATA_CODE_COMMIT=$QDATA_COMMIT COMPANY_CODE_COMMIT=$RELEASE_COMMIT
USER 10001:10001
CMD ["quant-company", "worker"]

FROM base AS maintenance-tools
# Only the fixed GitHub App JWT signer invokes this binary; candidate code is never executed here.
RUN apt-get update && apt-get install --yes --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/*

FROM maintenance-tools AS maintenance
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable --extra lake
COPY deploy/entrypoint.py /opt/company/entrypoint.py
ARG RELEASE_COMMIT=unqualified
ARG QDATA_COMMIT=unqualified
LABEL org.opencontainers.image.revision=$RELEASE_COMMIT org.quant-company.qdata-revision=$QDATA_COMMIT
ENV QDATA_CODE_COMMIT=$QDATA_COMMIT COMPANY_CODE_COMMIT=$RELEASE_COMMIT
USER 10001:10001
CMD ["quant-company", "maintenance", "--config", "/etc/quant-company/maintenance.json"]

FROM base AS codex-tools
COPY --from=codex-dist /usr/local/bin/node /usr/local/bin/node
COPY --from=codex-dist /usr/local/lib/node_modules/@openai /usr/local/lib/node_modules/@openai
RUN ln -s /usr/local/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && mkdir -p /state/auth /state/jobs \
    && chown -R 10001:10001 /state

FROM codex-tools AS codex
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable --extra lake \
    && python -c 'import subprocess; from quant_company.providers.codex_release import SUPPORTED_CLI_VERSION; assert subprocess.check_output(["codex", "--version"], text=True).strip() == "codex-cli " + SUPPORTED_CLI_VERSION'
COPY deploy/entrypoint.py /opt/company/entrypoint.py
ARG RELEASE_COMMIT=unqualified
ARG QDATA_COMMIT=unqualified
LABEL org.opencontainers.image.revision=$RELEASE_COMMIT org.quant-company.qdata-revision=$QDATA_COMMIT
ENV QDATA_CODE_COMMIT=$QDATA_COMMIT COMPANY_CODE_COMMIT=$RELEASE_COMMIT \
    CODEX_BIN=codex CODEX_HOME=/state/auth CODEX_JOBS_DIR=/state/jobs
USER 10001:10001
CMD ["uvicorn", "quant_company.providers.codex_runtime:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

FROM base AS claude-tools
COPY --from=claude-dist /claude /usr/local/bin/claude

FROM claude-tools AS claude
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable --extra lake
COPY deploy/entrypoint.py /opt/company/entrypoint.py
ARG RELEASE_COMMIT=unqualified
ARG QDATA_COMMIT=unqualified
LABEL org.opencontainers.image.revision=$RELEASE_COMMIT org.quant-company.qdata-revision=$QDATA_COMMIT
ENV QDATA_CODE_COMMIT=$QDATA_COMMIT COMPANY_CODE_COMMIT=$RELEASE_COMMIT \
    CLAUDE_CONFIG_DIR=/state/auth CLAUDE_JOBS_DIR=/state/jobs DISABLE_AUTOUPDATER=1
USER 10001:10001
CMD ["uvicorn", "quant_company.providers.claude_runtime:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

# The media worker: FFmpeg, CJK fonts, the video extra and Chromium for frame capture.
FROM base AS video-tools
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright HF_HOME=/state/models \
    OMP_NUM_THREADS=2 TOKENIZERS_PARALLELISM=false
RUN --mount=type=cache,target=/root/.cache/uv apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg fonts-noto-cjk \
    && uv sync --frozen --no-dev --no-editable --no-install-project --extra lake --extra video \
    && /app/.venv/bin/playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /opt/playwright

FROM video-tools AS video
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable --extra lake --extra video
COPY deploy/entrypoint.py /opt/company/entrypoint.py
ARG RELEASE_COMMIT=unqualified
ARG QDATA_COMMIT=unqualified
LABEL org.opencontainers.image.revision=$RELEASE_COMMIT org.quant-company.qdata-revision=$QDATA_COMMIT
ENV QDATA_CODE_COMMIT=$QDATA_COMMIT COMPANY_CODE_COMMIT=$RELEASE_COMMIT
USER 10001:10001
CMD ["quant-company", "video-worker"]
