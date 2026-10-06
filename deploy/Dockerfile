# Build targets: app and codex. Release builds require the committed uv.lock.
FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS codex-dist
COPY src/quant_company/providers/codex_release.json /tmp/codex_release.json
COPY deploy/install-codex.cjs /tmp/install-codex.cjs
RUN node /tmp/install-codex.cjs /tmp/codex_release.json

FROM ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa AS uv-dist
FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS app
ARG RELEASE_COMMIT=unqualified
ARG QDATA_COMMIT=unqualified
LABEL org.opencontainers.image.revision=$RELEASE_COMMIT
LABEL org.quant-company.qdata-revision=$QDATA_COMMIT
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH=/app/.venv/bin:$PATH QDATA_CODE_COMMIT=$QDATA_COMMIT \
    COMPANY_CODE_COMMIT=$RELEASE_COMMIT
COPY --from=uv-dist /uv /usr/local/bin/uv
# Preserve the sibling qdata path recorded in uv.lock.
WORKDIR /opt/company
RUN ln -s /opt/company /app
COPY --from=qdata pyproject.toml README.md /opt/quant-data/
COPY --from=qdata src /opt/quant-data/src
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --extra lake \
    && groupadd --gid 10001 company \
    && useradd --uid 10001 --gid 10001 --create-home company
COPY deploy/entrypoint.py /opt/company/entrypoint.py
USER 10001:10001
ENTRYPOINT ["python", "/app/entrypoint.py"]
CMD ["quant-company", "serve", "--host", "0.0.0.0", "--port", "8000"]

FROM app AS autonomous-research
USER root
# Trusted Git operations build scoped research bundles and verify the pinned qlab checkout.
# Candidate code executes only in the 3070 sandbox, never in this image.
RUN apt-get update && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && /app/.venv/bin/python -c "import yaml; assert yaml.__version__ == '6.0.3'"
USER 10001:10001
CMD ["quant-company", "worker"]

FROM app AS maintenance
USER root
# Only the fixed GitHub App JWT signer invokes this binary; candidate code is never executed here.
RUN apt-get update && apt-get install --yes --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/*
USER 10001:10001
CMD ["quant-company", "maintenance", "--config", "/etc/quant-company/maintenance.json"]

FROM app AS codex
USER root
COPY --from=codex-dist /usr/local/bin/node /usr/local/bin/node
COPY --from=codex-dist /usr/local/lib/node_modules/@openai /usr/local/lib/node_modules/@openai
RUN ln -s /usr/local/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && python -c 'import subprocess; from quant_company.providers.codex_release import SUPPORTED_CLI_VERSION; assert subprocess.check_output(["codex", "--version"], text=True).strip() == "codex-cli " + SUPPORTED_CLI_VERSION' \
    && mkdir -p /state/auth /state/jobs \
    && chown -R 10001:10001 /state
USER 10001:10001
ENV CODEX_BIN=codex CODEX_HOME=/state/auth CODEX_JOBS_DIR=/state/jobs
CMD ["uvicorn", "quant_company.providers.codex_runtime:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

# This qualified native distribution targets the existing Linux x86_64 office host.
FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS claude-dist
RUN npm install --global --omit=dev @anthropic-ai/claude-code@2.1.275 \
    && install -m 0755 "$(readlink -f /usr/local/bin/claude)" /claude \
    && echo '13586f3150a7ca1655f36e1dba759fb404e0f7cf7021d4a3dcd5e6f604e56156  /claude' | sha256sum -c - \
    && /claude --version | grep -Fx '2.1.275 (Claude Code)'

FROM app AS claude
COPY --from=claude-dist /claude /usr/local/bin/claude
ENV CLAUDE_CONFIG_DIR=/state/auth CLAUDE_JOBS_DIR=/state/jobs DISABLE_AUTOUPDATER=1
CMD ["uvicorn", "quant_company.providers.claude_runtime:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
