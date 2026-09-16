# Build targets: app and codex. Release builds require the committed uv.lock.
FROM node:22-bookworm-slim AS codex-dist
RUN npm install --global --omit=dev @openai/codex@0.154.0 \
    && codex --version | grep -Fx 'codex-cli 0.154.0'

FROM ghcr.io/astral-sh/uv:0.11.28 AS uv-dist
FROM python:3.12-slim-bookworm AS app
ARG RELEASE_COMMIT=unqualified
LABEL org.opencontainers.image.revision=$RELEASE_COMMIT
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH=/app/.venv/bin:$PATH
COPY --from=uv-dist /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable \
    && groupadd --gid 10001 company \
    && useradd --uid 10001 --gid 10001 --create-home company
COPY deploy/entrypoint.py /app/entrypoint.py
USER 10001:10001
ENTRYPOINT ["python", "/app/entrypoint.py"]
CMD ["quant-company", "serve", "--host", "0.0.0.0", "--port", "8000"]

FROM app AS codex
USER root
COPY --from=codex-dist /usr/local/bin/node /usr/local/bin/node
COPY --from=codex-dist /usr/local/lib/node_modules/@openai /usr/local/lib/node_modules/@openai
RUN ln -s /usr/local/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && codex --version | grep -Fx 'codex-cli 0.154.0' \
    && mkdir -p /state/auth /state/jobs \
    && chown -R 10001:10001 /state
USER 10001:10001
ENV CODEX_BIN=codex CODEX_HOME=/state/auth CODEX_JOBS_DIR=/state/jobs
CMD ["uvicorn", "quant_company.providers.codex_runtime:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
