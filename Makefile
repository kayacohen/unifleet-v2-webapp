# Makefile — convenience targets for the local Docker Compose dev stack.
# Run `make` or `make help` to see this list.

COMPOSE := docker compose

.DEFAULT_GOAL := help

.PHONY: help up up-d down clean logs shell psql verify test restart build

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
