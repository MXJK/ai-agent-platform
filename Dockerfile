FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git libgomp1 nodejs npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.self-hosted.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.self-hosted.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY ai_agent_platform ./ai_agent_platform
COPY pyproject.toml ./

RUN python -m pip install --no-deps --no-build-isolation .

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home app \
    && mkdir -p /workspaces /var/lib/ai-agent-platform /home/app/.cache/huggingface /home/app/.cogent \
    && chown -R app:app /app /workspaces /var/lib/ai-agent-platform /home/app/.cache /home/app/.cogent

USER app

RUN python -c "import ai_agent_platform.api.entrypoint"

EXPOSE 8000

CMD ["python", "-m", "ai_agent_platform.api.entrypoint", "--host", "0.0.0.0", "--port", "8000"]
