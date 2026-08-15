#!/bin/bash
set -e

cp -f .env.cn_prod .env.prod

docker compose -f docker-compose.cn-prod.yml down

sleep 1s

docker compose -f docker-compose.cn-prod.yml up -d --build
