#!/bin/bash
set -e

rm -rf .env

cp -f .env.cn_test .env

docker compose down

sleep 1s

docker compose up -d
