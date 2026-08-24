ARG UV_VERSION=0.11.26
ARG NODE_VERSION=24-slim
ARG PYTHON_VERSION=3.13-slim

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM node:${NODE_VERSION} AS gateway

ARG SUPERGATEWAY_VERSION=3.4.3

RUN npm install --global "supergateway@${SUPERGATEWAY_VERSION}" \
    && npm cache clean --force

FROM python:${PYTHON_VERSION}

ARG MINIMAX_MCP_VERSION=0.0.4

COPY --from=uv /uv /usr/local/bin/uv
COPY --from=gateway /usr/local/bin/node /usr/local/bin/node
COPY --from=gateway /usr/local/lib/node_modules/supergateway /usr/local/lib/node_modules/supergateway

RUN ln -s /usr/local/lib/node_modules/supergateway/dist/index.js /usr/local/bin/supergateway \
    && uv venv /opt/minimax-mcp \
    && uv pip install \
        --no-cache \
        --python /opt/minimax-mcp/bin/python \
        "mcp[cli]==1.29.0" \
        "minimax-coding-plan-mcp==${MINIMAX_MCP_VERSION}" \
    && sed -i '/print("Starting Minimax MCP server")/d' \
        /opt/minimax-mcp/lib/python*/site-packages/minimax_mcp/server.py

COPY --chmod=755 entrypoint.sh /usr/local/bin/minimax-mcp-entrypoint

ENV MINIMAX_API_HOST=https://api.minimaxi.com \
    FASTMCP_LOG_LEVEL=WARNING \
    HOME=/tmp

EXPOSE 8000

USER 65532:65532

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD node -e "fetch('http://127.0.0.1:8000/healthz').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"

ENTRYPOINT ["minimax-mcp-entrypoint"]
