#!/bin/bash
set -e

rm -rf .env.prod

cp -f .env.cn_prod .env.prod

docker compose -f docker-compose.cn-prod.yml --env-file .env.prod down

sleep 1s

docker compose -f docker-compose.cn-prod.yml --env-file .env.prod up -d --build
