# Multi-service runtime image for cTrader services on the Azure VM.
# All Python packages live inside this directory — no external mounts needed.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# System dependencies: supervisor for multi-process, curl/netcat for health checks.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    supervisor \
    curl \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Prepare directories.
RUN mkdir -p /app/config /app/logs /var/log/supervisor

# Copy dependency manifest and install.
COPY pyproject.toml /app/
RUN python - <<'PY'
import tomllib
from pathlib import Path

def extract_deps(path: Path) -> list[str]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    project = data.get("project", {})
    deps = list(project.get("dependencies", []))
    extras = project.get("optional-dependencies", {})
    for group in ("dev", "appwrite", "mongodb", "influxdb", "test"):
        deps.extend(extras.get(group, []))
    return deps

deps = extract_deps(Path("/app/pyproject.toml"))
Path("/app/requirements.txt").write_text("\n".join(deps) + "\n", encoding="utf-8")
print(f"Extracted {len(deps)} dependencies")
PY

RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy supervisor config and runner scripts.
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY bin/* /usr/local/bin/
RUN chmod +x /usr/local/bin/run-*

# Copy all Python source packages.
COPY ssfx_parser/ /app/ssfx_parser/
COPY ctrader_client/ /app/ctrader_client/
COPY ctrader_cli/ /app/ctrader_cli/
COPY ssfx_trader/ /app/ssfx_trader/
COPY ssfx_server/ /app/ssfx_server/
COPY ctrader/ /app/ctrader/
COPY market_data_service/ /app/market_data_service/

EXPOSE 8000 9000 9001 9002 9300 9301

CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
