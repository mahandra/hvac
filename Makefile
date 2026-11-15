SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE  ?= docker compose
WEB      := $(COMPOSE) exec web
MANAGE   := $(WEB) python manage.py
XLSX     ?= /app/data/import/properties.xlsx
PHOTOS   ?= /app/data/import/Photo
STAMP    := $(shell date +%Y%m%d-%H%M%S)

TESTDB_NAME := hvac-testdb
TESTDB_PORT := 55432

.PHONY: help up down build restart rebuild ps logs shell dbshell superuser migrate \
        import-properties import-photos backup restore test update mcp

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- stack ------------------------------------------------------------------------------

up: ## Start the stack in the background (http://<host>:8001/)
	$(COMPOSE) up -d

down: ## Stop and remove containers (data in ./data is kept)
	$(COMPOSE) down

build: ## Build the web image
	$(COMPOSE) build web

rebuild: ## Rebuild the web image and restart it
	$(COMPOSE) up -d --build

restart: ## Restart the web container
	$(COMPOSE) restart web

ps: ## Show container status
	$(COMPOSE) ps

logs: ## Follow web logs (Ctrl+C to stop)
	$(COMPOSE) logs -f --tail=100 web

shell: ## Django shell inside the web container
	$(MANAGE) shell

dbshell: ## psql inside the db container
	$(COMPOSE) exec db sh -c 'psql -U "$$POSTGRES_USER" "$$POSTGRES_DB"'

# --- admin tasks ------------------------------------------------------------------------

superuser: ## Create a Django superuser (interactive)
	$(MANAGE) createsuperuser

mcp: ## Run the MCP server over stdio (normally started by the MCP client, see README)
	@docker compose exec -T web python manage.py mcp_server

migrate: ## Apply database migrations
	$(MANAGE) migrate

import-properties: ## Import the spreadsheet (XLSX=/app/data/import/properties.xlsx)
	$(MANAGE) import_properties $(XLSX)

import-photos: ## Import the photo folders (PHOTOS=/app/data/import/Photo)
	$(MANAGE) import_photos $(PHOTOS)

# --- backup -----------------------------------------------------------------------------

backup: ## Dump the database and archive data/media into ./backups
	@mkdir -p backups
	$(COMPOSE) exec -T db sh -c 'pg_dump -U "$$POSTGRES_USER" -Fc "$$POSTGRES_DB"' > backups/hvac-$(STAMP).dump
	tar czf backups/media-$(STAMP).tar.gz -C data media
	@echo "Backup written: backups/hvac-$(STAMP).dump, backups/media-$(STAMP).tar.gz"

restore: ## Restore a backup: make restore DB=backups/hvac-X.dump [MEDIA=backups/media-X.tar.gz]
	@test -n "$(DB)" || { echo "Usage: make restore DB=backups/hvac-X.dump [MEDIA=backups/media-X.tar.gz]"; exit 1; }
	@test -f "$(DB)" || { echo "File not found: $(DB)"; exit 1; }
	@read -r -p "This overwrites the current database$(if $(MEDIA), and media files). Continue? [y/N] " answer; \
		[ "$$answer" = y ] || { echo "Aborted."; exit 1; }
	$(COMPOSE) exec -T db sh -c 'pg_restore -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" --clean --if-exists --no-owner' < "$(DB)"
	$(if $(MEDIA),tar xzf "$(MEDIA)" -C data)
	@echo "Restore finished."

# --- development ------------------------------------------------------------------------

test: ## Run the test suite against a throwaway Postgres 17 container
	@docker rm -f $(TESTDB_NAME) >/dev/null 2>&1 || true
	@docker run -d --rm --name $(TESTDB_NAME) -e POSTGRES_USER=hvac -e POSTGRES_PASSWORD=hvac \
		-e POSTGRES_DB=hvac -p 127.0.0.1:$(TESTDB_PORT):5432 postgres:17-alpine >/dev/null
	@until docker exec $(TESTDB_NAME) pg_isready -h 127.0.0.1 -U hvac >/dev/null 2>&1; do sleep 1; done
	@POSTGRES_PORT=$(TESTDB_PORT) uv run pytest $(ARGS); status=$$?; \
		docker rm -f $(TESTDB_NAME) >/dev/null; exit $$status

update: ## Pull new code, rebuild and restart (back up first!)
	git pull
	$(COMPOSE) build web
	$(COMPOSE) up -d
