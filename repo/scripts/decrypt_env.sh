#!/usr/bin/env bash
# Decrypt .env.age back to .env for use with docker-compose.
# Requires: age (https://github.com/FiloSottile/age)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENC_FILE="${SCRIPT_DIR}/../.env.age"
ENV_FILE="${SCRIPT_DIR}/../.env"

if [ ! -f "$ENC_FILE" ]; then
    echo "Error: $ENC_FILE not found" >&2
    exit 1
fi

age -d -o "$ENV_FILE" "$ENC_FILE"
echo "Decrypted .env.age -> .env"
