FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
WORKDIR /app
ENV PYTHONUNBUFFERED=1 UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export SSL_CERT_FILE=/run/secrets/build_ca; fi; \
    uv sync --locked --no-dev --no-install-project
COPY --chown=10001:10001 backend/agentproof ./backend/agentproof
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export SSL_CERT_FILE=/run/secrets/build_ca; fi; \
    uv sync --locked --no-dev
ENV PATH="/app/.venv/bin:${PATH}"
USER 10001:10001
EXPOSE 8000
