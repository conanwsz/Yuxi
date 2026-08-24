#!/bin/sh
set -eu

if [ -z "${MINIMAX_API_KEY:-}" ]; then
    echo "MINIMAX_API_KEY is required" >&2
    exit 1
fi

exec supergateway \
    --stdio "/opt/minimax-mcp/bin/minimax-coding-plan-mcp" \
    --outputTransport streamableHttp \
    --streamableHttpPath /mcp \
    --healthEndpoint /healthz \
    --port 8000 \
    --logLevel "${MCP_LOG_LEVEL:-info}"
