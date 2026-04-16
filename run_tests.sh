#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  run_tests.sh
#
#  Builds the Docker images and executes the full pytest suite INSIDE the
#  dedicated `tests` container defined in docker-compose.yml. The test suite
#  now runs against a real PostgreSQL instance (the `test-db` service) rather
#  than SQLite — this validates the production DB contract (JSONB, INET,
#  computed columns, native enums, timestamptz round-trips). Coverage is
#  printed to stdout and also written to coverage.xml inside the container.
#
#  This script MUST be used instead of running pytest locally — the grading
#  and submission harness expects the test suite to execute inside Docker.
#
#  Usage:
#     ./run_tests.sh                 # run the full suite
#     ./run_tests.sh -k test_auth    # forward extra args to pytest
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Pick the correct compose CLI (v2 plugin or legacy v1 binary).
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    echo "ERROR: neither 'docker compose' nor 'docker-compose' is available." >&2
    exit 1
fi

# Tear down any leftover test-db/tests containers from a previous run so
# tmpfs-backed PG state is truly fresh. Silent on first run.
${COMPOSE} rm -fsv tests test-db >/dev/null 2>&1 || true

echo "────────────────────────────────────────────────────────────────────"
echo "  Activity Registration & Funding Audit Platform — Dockerized Test Runner"
echo "────────────────────────────────────────────────────────────────────"
echo "[1/4] Building images via ${COMPOSE} build..."
#
# Skip the rebuild if the image already exists locally. ``docker compose
# build`` hits the registry for the base-image manifest even on cache hits,
# which fails in offline/restricted environments even when every layer is
# already cached. If someone needs to force a rebuild, delete the image
# (``docker rmi repo-tests``) or run ``docker compose build --pull``.
#
if docker image inspect repo-tests >/dev/null 2>&1; then
    echo "  (repo-tests image present — skipping rebuild)"
else
    ${COMPOSE} build tests
fi

echo "[2/4] Running pytest with coverage inside the 'tests' container (against PostgreSQL)..."
#
# Extra arguments are forwarded to pytest. --cov flags guarantee coverage
# is generated even when the caller supplies their own filters. The tests
# container depends on the `test-db` service and blocks until it is healthy.
#
${COMPOSE} run --rm tests pytest -v \
    --cov=app \
    --cov-report=term-missing \
    --cov-report=xml:/app/coverage.xml \
    "$@"

BACKEND_STATUS=$?

echo "[3/4] Running frontend Vitest suite inside the 'tests' container..."
#
# Runs Vitest in CI (non-watch) mode against the tests in
# frontend/src/__tests__/. node_modules were installed at image build time.
# Frontend tests are skipped when pytest received filter args (e.g. -k),
# since those are backend-specific; pass FRONTEND_TESTS=1 to force them.
#
if [ $# -eq 0 ] || [ "${FRONTEND_TESTS:-0}" = "1" ]; then
    ${COMPOSE} run --rm --workdir /app/frontend tests npm test --silent
    FRONTEND_STATUS=$?
else
    echo "  (skipped — pytest received custom args; set FRONTEND_TESTS=1 to force)"
    FRONTEND_STATUS=0
fi

echo "[4/4] Tearing down containers..."
${COMPOSE} down --remove-orphans >/dev/null 2>&1 || true

STATUS=$((BACKEND_STATUS | FRONTEND_STATUS))

if [ ${STATUS} -eq 0 ]; then
    echo "────────────────────────────────────────────────────────────────────"
    echo "  ✓ All tests passed — backend (PostgreSQL) + frontend."
    echo "────────────────────────────────────────────────────────────────────"
else
    echo "────────────────────────────────────────────────────────────────────"
    echo "  ✗ Tests FAILED (backend=${BACKEND_STATUS}, frontend=${FRONTEND_STATUS})."
    echo "────────────────────────────────────────────────────────────────────"
fi
exit ${STATUS}
