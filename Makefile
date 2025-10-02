
    .PHONY: dev up build seed alembic

    dev:
\tcd docker/compose && docker compose up --build

    up:
\tcd docker/compose && docker compose up -d --build

    seed:
\tcd apps/server && . .venv/bin/activate && RELABEL_ADMIN_PASSWORD=admin123 RELABEL_CLIENT_CODE=123456 python scripts/dev_seed.py

    alembic:
\tcd apps/server && . .venv/bin/activate && alembic upgrade head
