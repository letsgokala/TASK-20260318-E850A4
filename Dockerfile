# Stage 1: Build the Vue.js frontend
FROM node:20-slim AS frontend-builder

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# Stage 2: Python application
FROM python:3.11-slim

WORKDIR /app

# Restore tooling must live inside the same image that serves the admin
# API, because POST /api/v1/admin/backups/{date}/restore shells out to
# ``pg_restore`` and ``rsync`` (app/api/v1/admin_ops.py). The audit
# previously flagged one-click restore as not self-contained because
# the slim Python base had neither of these binaries installed, which
# made the prompt's "one-click recovery" guarantee un-deliverable.
#
# - ``postgresql-client`` provides ``pg_restore`` (and ``pg_dump``, useful
#   for ad-hoc ops) and must match the PostgreSQL 16 server version in
#   ``docker-compose.yml``. Debian bookworm-slim carries PG 15 in its
#   default repo, so pull the matching client from the pgdg apt source.
# - ``rsync`` restores the on-disk material/invoice tree back into
#   ``/storage`` during a one-click restore.
# - ``age`` and ``gnupg`` are optional; ``age`` is referenced by
#   ``scripts/decrypt_env.sh`` so operators running the
#   encrypted-config workflow have the binary available inside the
#   container. ``gnupg`` is needed to trust the pgdg signing key.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg rsync age; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        | gpg --dearmor -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg; \
    . /etc/os-release; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg] https://apt.postgresql.org/pub/repos/apt/ ${VERSION_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends postgresql-client-16; \
    apt-get purge -y --auto-remove curl gnupg; \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Copy the built frontend from the builder stage
COPY --from=frontend-builder /frontend/dist /app/frontend/dist

EXPOSE 8000

# Entrypoint resolves optional age-encrypted .env before handing off to
# uvicorn. The entrypoint is a no-op when no encrypted config is
# present, preserving the plaintext .env path for default deployments.
COPY scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
