#!/bin/bash
set -e

cp -f .env.cn_test .env

docker compose down

sleep 1s

docker compose up -d
