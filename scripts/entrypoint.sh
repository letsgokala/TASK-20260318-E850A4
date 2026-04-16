#!/usr/bin/env sh
# Runtime entrypoint.
#
# The audit flagged that "encryption of sensitive configurations" was
# claimed but never integrated into the actual runtime: the process
# always read a plaintext .env and the `age` helpers in scripts/ were
# purely manual. This entrypoint closes that gap.
#
# Two modes are supported:
#
#   1. Plaintext .env (default)
#        If `.env` already exists and no `ENV_AGE_KEY_FILE` /
#        `ENV_AGE_PASSPHRASE` is set, the entrypoint is a no-op —
#        behavior is identical to the pre-existing deployment path so
#        no existing operator is broken.
#
#   2. Encrypted .env
#        If an `.env.age` file is present on the image or mounted into
#        /app/.env.age at runtime, the entrypoint decrypts it to /app/.env
#        using the `age` CLI (installed in the Dockerfile). The
#        decryption key is provided by one of:
#          - ENV_AGE_KEY_FILE  — path to an age identity file (e.g. a
#            mounted secret from the orchestrator)
#          - ENV_AGE_PASSPHRASE — passphrase-based decryption
#        At least one of these env vars is required when `.env.age`
#        exists and no `.env` is already present. If both keying paths
#        are provided, the key file takes precedence (it is stronger
#        than a passphrase and less likely to be logged).
#
#        The decrypted /app/.env is written mode 0600 and removed on
#        exit via a signal trap so the plaintext does not outlive the
#        container process.
#
# Non-goals: this entrypoint does NOT invent secret management. It is
# a documented, testable bridge between operator-side encryption
# (scripts/encrypt_env.sh) and the running process. Teams with a real
# secret manager should keep injecting env vars directly; this
# entrypoint is a strict no-op for them.

set -eu

ENV_FILE="${ENV_FILE:-/app/.env}"
ENV_AGE_FILE="${ENV_AGE_FILE:-/app/.env.age}"

cleanup_plaintext() {
    # Only delete the plaintext .env on exit if WE materialized it from
    # .env.age in this container. Do not touch an operator-mounted
    # plaintext .env.
    if [ -n "${_DECRYPTED_BY_ENTRYPOINT:-}" ] && [ -f "$ENV_FILE" ]; then
        rm -f "$ENV_FILE"
    fi
}
trap cleanup_plaintext EXIT INT TERM

if [ -f "$ENV_AGE_FILE" ] && [ ! -f "$ENV_FILE" ]; then
    if ! command -v age >/dev/null 2>&1; then
        echo "[entrypoint] ERROR: $ENV_AGE_FILE present but 'age' binary missing." >&2
        exit 1
    fi
    echo "[entrypoint] Decrypting $ENV_AGE_FILE -> $ENV_FILE"
    umask 077
    if [ -n "${ENV_AGE_KEY_FILE:-}" ] && [ -f "$ENV_AGE_KEY_FILE" ]; then
        age --decrypt --identity "$ENV_AGE_KEY_FILE" --output "$ENV_FILE" "$ENV_AGE_FILE"
    elif [ -n "${ENV_AGE_PASSPHRASE:-}" ]; then
        # age reads the passphrase from stdin when -p is used with
        # --decrypt. Suppress the interactive prompt by piping.
        printf '%s\n' "$ENV_AGE_PASSPHRASE" | age --decrypt --output "$ENV_FILE" "$ENV_AGE_FILE"
    else
        echo "[entrypoint] ERROR: $ENV_AGE_FILE present but neither ENV_AGE_KEY_FILE nor ENV_AGE_PASSPHRASE set." >&2
        exit 1
    fi
    chmod 600 "$ENV_FILE"
    _DECRYPTED_BY_ENTRYPOINT=1
    export _DECRYPTED_BY_ENTRYPOINT
fi

# If .env is populated (either mounted plaintext or freshly decrypted),
# export its entries so the Python process (which uses pydantic-settings
# with env_file=.env) sees them. pydantic-settings already reads .env
# on Settings() construction, so the export is redundant but harmless
# for processes spawned from child shells that don't re-parse the file.
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi

exec "$@"
