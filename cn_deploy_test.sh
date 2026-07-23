#!/bin/bash
set -e

docker compose down

sleep 1s

docker compose up -d
