FROM python:3.11-slim AS builder
WORKDIR /app

# Build tooling
RUN pip install --no-cache-dir --upgrade pip build hatchling

# Copy sources needed for wheel build
COPY pyproject.toml README.md /app/
COPY src /app/src

# Build a wheel
RUN python -m build --wheel

FROM python:3.11-slim AS runtime
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install the built wheel
COPY --from=builder /app/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && \
    rm -rf /tmp/*.whl

ENTRYPOINT ["stonks"]
