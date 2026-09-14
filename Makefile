.PHONY: up down logs build migrate revision seed test lint fmt shell psql reset

up:        ; docker compose up -d --build && docker compose logs -f api
down:      ; docker compose down
logs:      ; docker compose logs -f api
build:     ; docker compose build
migrate:   ; docker compose exec api alembic upgrade head
revision:  ; docker compose exec api alembic revision --autogenerate -m "$(m)"
seed:      ; docker compose exec api python /srv/scripts/seed.py
test:      ; docker compose run --rm api pytest -v
lint:      ; docker compose run --rm api ruff check .
fmt:       ; docker compose run --rm api ruff format .
shell:     ; docker compose exec api bash
psql:      ; docker compose exec postgres psql -U unigate -d unigate
reset:     ; docker compose down -v && docker compose up -d --build
