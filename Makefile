# Makefile — convenience targets for the local Docker Compose dev stack.
# Run `make` or `make help` to see this list.

COMPOSE := docker compose

.DEFAULT_GOAL := help

.PHONY: help up up-d down clean logs shell psql verify test test-db backup restore-pg restore-list backup-clean restart build

help: ## Show this help
	@printf "Local dev targets:\n"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Build and start the stack in the foreground
	$(COMPOSE) up --build

up-d: ## Build and start the stack in the background
	$(COMPOSE) up --build -d

down: ## Stop the stack, keep the Postgres volume
	$(COMPOSE) down

clean: ## Stop the stack AND drop the Postgres volume (full reset)
	$(COMPOSE) down -v

restart: ## Restart the web service (picks up code changes via bind-mount)
	$(COMPOSE) restart web

build: ## Rebuild images without starting
	$(COMPOSE) build

logs: ## Tail logs from the web service
	$(COMPOSE) logs -f web

shell: ## Open a bash shell in the web container
	$(COMPOSE) exec web bash

psql: ## Open psql in the db container
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-unifleet} $${POSTGRES_DB:-unifleet}

verify: ## Run the F1.1 build probe inside the web container
	$(COMPOSE) exec web python scripts/verify_build.py

test: ## Run the pytest suite in a clean one-shot container (no Postgres needed)
	docker run --rm -v "$(PWD):/app" -w /app python:3.11-slim sh -c \
	  "pip install poetry && poetry install && poetry run pytest"

test-db: ## Run pytest inside the web container (uses unifleet-db on the docker network)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.test.yml run --rm web \
	  sh -c "pip install --quiet pytest && pytest"

# --- Backup / Restore (local dev) ----------------------------------------
# Local backups use the postgres:16-alpine image's pg_dump, streamed
# over the compose network to a host file. The same dump format is
# used in production (Railway Cron Schedule) — see scripts/backup_postgres.py
# and specs/plans/PLAN-pg-backup.md.

BACKUP_DIR := data/legacy/backups
BACKUP_PREFIX := unifleet-

backup: ## Take a Postgres backup to $(BACKUP_DIR)/
	@mkdir -p $(BACKUP_DIR)
	@mkdir -p $(BACKUP_DIR) 2>/dev/null || { \
	  echo "ERROR: cannot create $(BACKUP_DIR)/ (parent dir may be root-owned)."; \
	  echo "  Either sudo chown -R $$USER data/legacy/, or override BACKUP_DIR=/some/writable/path"; \
	  exit 1; }
	@TS=$$(date -u +%Y%m%d-%H%M%S); \
	  $(COMPOSE) exec -T db pg_dump -U $${POSTGRES_USER:-unifleet} -d $${POSTGRES_DB:-unifleet} \
	    --format=custom --no-owner --no-privileges \
	    > $(BACKUP_DIR)/$(BACKUP_PREFIX)$${TS}.pgdump
	@echo "Backups in $(BACKUP_DIR)/:"
	@ls -lh $(BACKUP_DIR)/*.pgdump 2>/dev/null | tail -3

restore-list: ## List the contents of the latest backup
	@LATEST=$$(ls -t $(BACKUP_DIR)/*.pgdump 2>/dev/null | head -1); \
	  if [ -z "$$LATEST" ]; then echo "No backups in $(BACKUP_DIR)/"; exit 1; fi; \
	  echo "TOC of $$LATEST:"; \
	  $(COMPOSE) exec -T db pg_restore --list < $$LATEST 2>&1 | head -20

restore-pg: ## Restore the latest backup into a fresh DB (unifleet_restore). Does NOT touch the live DB.
	@LATEST=$$(ls -t $(BACKUP_DIR)/*.pgdump 2>/dev/null | head -1); \
	  if [ -z "$$LATEST" ]; then echo "No backups in $(BACKUP_DIR)/"; exit 1; fi; \
	  echo "Restoring $$LATEST into unifleet_restore..."; \
	  $(COMPOSE) exec -T db psql -U $${POSTGRES_USER:-unifleet} -d postgres -c "DROP DATABASE IF EXISTS unifleet_restore;"; \
	  $(COMPOSE) exec -T db psql -U $${POSTGRES_USER:-unifleet} -d postgres -c "CREATE DATABASE unifleet_restore;"; \
	  $(COMPOSE) exec -T db pg_restore -U $${POSTGRES_USER:-unifleet} -d unifleet_restore --no-owner --no-privileges --clean --if-exists < $$LATEST; \
	  echo "Row counts in restored DB:"; \
	  $(COMPOSE) exec -T db psql -U $${POSTGRES_USER:-unifleet} -d unifleet_restore -c "\
	    SELECT 'stations' AS t, COUNT(*) FROM stations \
	    UNION ALL SELECT 'prices', COUNT(*) FROM prices \
	    UNION ALL SELECT 'discounts', COUNT(*) FROM discounts \
	    UNION ALL SELECT 'customers', COUNT(*) FROM customers \
	    UNION ALL SELECT 'vouchers', COUNT(*) FROM vouchers \
	    UNION ALL SELECT 'audit_log', COUNT(*) FROM audit_log;"

backup-clean: ## Remove ALL local backups (DESTRUCTIVE; cannot be undone)
	@echo "Removing all files in $(BACKUP_DIR)/..."
	@rm -f $(BACKUP_DIR)/*.pgdump $(BACKUP_DIR)/backup.log
	@echo "Done."
