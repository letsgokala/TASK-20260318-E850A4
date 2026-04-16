#!/usr/bin/env bash
# Encrypt the .env file using age and securely delete the plaintext.
# Requires: age (https://github.com/FiloSottile/age)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../.env"
ENC_FILE="${SCRIPT_DIR}/../.env.age"

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: $ENV_FILE not found" >&2
    exit 1
fi

age -p -o "$ENC_FILE" "$ENV_FILE"
shred -u "$ENV_FILE"
echo "Encrypted .env -> .env.age and securely deleted plaintext."
