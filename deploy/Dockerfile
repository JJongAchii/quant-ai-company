# Build targets: app and codex. Release builds require the committed uv.lock.
FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS codex-dist
RUN npm install --global --omit=dev @openai/codex@0.154.0 \
    && codex --version | grep -Fx 'codex-cli 0.154.0'

FROM ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa AS uv-dist
FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS app
ARG RELEASE_COMMIT=unqualified
ARG QDATA_COMMIT=unqualified
LABEL org.opencontainers.image.revision=$RELEASE_COMMIT
LABEL org.quant-company.qdata-revision=$QDATA_COMMIT
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH=/app/.venv/bin:$PATH QDATA_CODE_COMMIT=$QDATA_COMMIT
COPY --from=uv-dist /uv /usr/local/bin/uv
# Preserve the two-level relative qdata path recorded in uv.lock.
WORKDIR /opt/company
RUN ln -s /opt/company /app
COPY --from=qdata pyproject.toml README.md /quant-data/
COPY --from=qdata src /quant-data/src
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --extra lake \
    && groupadd --gid 10001 company \
    && useradd --uid 10001 --gid 10001 --create-home company
COPY deploy/entrypoint.py /opt/company/entrypoint.py
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
