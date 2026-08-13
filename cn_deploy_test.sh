#!/bin/bash
set -e

docker compose down

sleep 1s

cp -f .env.cn_test .env

docker compose up -d
