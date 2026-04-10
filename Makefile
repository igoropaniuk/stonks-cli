.DEFAULT_GOAL := help
SHELL         := /bin/bash
.ONESHELL:
.SHELLFLAGS   := -eu -o pipefail -c

IMAGE     := stonks

.PHONY: help init run run-demo check-all test type format check ascii docker-build docker-run docker-demo

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sed 's/:.*## /\t/' \
		| column -ts$$'\t'

init: ## Install all dependencies (including dev)
	uv sync

run: ## Run the application
	uv run stonks

run-demo: ## Launch the TUI with a sample demo portfolio
	uv run stonks demo

check-all: ascii check format-check type test ## Run all CI checks

ascii: ## Check for non-ASCII unicode symbols
	uv run bash ./scripts/check_unicode_symbols.sh --check --all-files

test: ## Run tests with coverage
	uv run pytest -q --cov --cov-report=xml --cov-report=term-missing

type: ## Run mypy type checker
	uv run mypy

format: ## Format code with ruff
	uv run ruff format .
	uv run ruff check . --fix

format-check: ## Check code formatting (CI mode)
	uv run ruff format --check .

check: ## Run ruff linter
	uv run ruff check .

docker-build: ## Build the Docker image
	docker build -t $(IMAGE) .

docker-run: docker-build ## Run the dashboard from portfolio.yaml in current dir
	docker run --rm -it -v ./portfolio.yaml:/data/portfolio.yaml:ro \
		$(IMAGE) --portfolio /data/portfolio.yaml dashboard

docker-demo: docker-build ## Run the demo portfolio in Docker
	docker run --rm -it $(IMAGE) demo
