# Pinned by digest so a rebuild gets the same base until it is bumped on
# purpose; the tag is kept for readability.
ARG PYTHON_IMAGE=python:3.14.10-slim-trixie@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8

FROM ${PYTHON_IMAGE} AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a /uv /bin/uv

# Install exactly what uv.lock pins, into a venv the runtime stage copies.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src

# Dependencies first, so source edits don't invalidate this layer
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

# Run as an unprivileged user; /data is where a portfolio directory is
# mounted (see README, "Running with Docker").
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin stonks && \
    install -d -o stonks -g stonks /data

COPY --from=builder /opt/venv /opt/venv

USER stonks
WORKDIR /home/stonks

ENTRYPOINT ["stonks"]
