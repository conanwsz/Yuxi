#!/bin/bash
set -e

docker compose -f docker-compose.cn-prod.yml down

sleep 1s

docker compose -f docker-compose.cn-prod.yml up -d --build
