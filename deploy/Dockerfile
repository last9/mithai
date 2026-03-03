# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Stage 1: build — venv built at /app/.venv so shebang paths match runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/

RUN python -m venv /app/.venv && \
    /app/.venv/bin/pip install --upgrade pip --quiet && \
    /app/.venv/bin/pip install ".[slack]" --quiet && \
    echo "=== entry point ===" && \
    head -1 /app/.venv/bin/mithai && \
    /app/.venv/bin/mithai --help

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ARG KUBECTL_VERSION=v1.29.3
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates jq procps && \
    curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
         -o /usr/local/bin/kubectl && \
    chmod +x /usr/local/bin/kubectl && \
    apt-get remove -y curl && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Venv built at /app/.venv in builder — shebang #!/app/.venv/bin/python3.11
# matches this path in the runtime stage too
COPY --from=builder /app/.venv /app/.venv
COPY skills/ /app/skills/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN mkdir -p /app/.mithai/state /app/memory

ENTRYPOINT ["mithai", "run", "--config", "/config/config.yaml"]
