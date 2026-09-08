FROM python:3.11-slim

ARG MCP_SERVER_QDRANT_VERSION=0.8.1
ARG QDRANT_CLIENT_VERSION=1.17.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/var/cache/fastembed

RUN groupadd --system mcp \
    && useradd --system --gid mcp --create-home mcp \
    && mkdir -p /var/cache/fastembed \
    && chown -R mcp:mcp /var/cache/fastembed \
    && pip install --no-cache-dir \
        "mcp-server-qdrant==${MCP_SERVER_QDRANT_VERSION}" \
        "qdrant-client==${QDRANT_CLIENT_VERSION}"

USER mcp

EXPOSE 8000

ENTRYPOINT ["mcp-server-qdrant"]
CMD ["--transport", "streamable-http"]
