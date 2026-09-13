SHELL := /bin/bash
VENV ?= .venv
# Worktrees sem .venv local: informe um venv existente com `make VENV=/caminho/da/venv
# test-policy`; se o venv local não existir, cai para python3 do PATH (ex.: aquele que
# já tem pytest/PyYAML). Não cria dependências novas para o repo.
PY := $(VENV)/bin/python
ifeq ($(wildcard $(PY)),)
PY := python3
endif
PIP := $(VENV)/bin/pip
SERVICES := identity fileimport playlist catalog bpm-match library report web

.PHONY: help setup venv contracts requirements run-web up down logs build test test-contract test-e2e test-policy lint clean run-%

help: ## Lista de comandos disponíveis
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: venv contracts requirements ## Prepara o ambiente local (venv + contracts + deps)

venv:
	python3 -m venv $(VENV)

contracts:
	$(PIP) install -e ./contracts

requirements: contracts
	@for s in $(SERVICES); do \
		$(PIP) install -q -r services/$$s/requirements.txt || exit 1; \
	done
	$(PIP) install -q pytest

port = $(shell $(PY) -c 'import sys; from spotfy_contracts.service import _SERVICE_DEFAULTS as d; print(d["$(1)"])')

run-%: ## Roda um serviço localmente: make run-<nome> (ex.: make run-identity)
	$(eval P := $(call port,$*))
	@echo "==> subindo $* em http://127.0.0.1:$(P)"
	$(VENV)/bin/uvicorn --app-dir services/$* main:app --host 127.0.0.1 --port $(P)

run-web: ## Sobe o BFF + UI em http://127.0.0.1:8000
	$(VENV)/bin/uvicorn --app-dir services/web main:app --host 127.0.0.1 --port 8000

up: ## Sobe o stack completo (docker compose)
	docker compose up --build -d

down: ## Derruba o stack
	docker compose down

logs: ## Logs do stack
	docker compose logs -f

test: test-contract test-e2e test-policy ## Roda todos os testes

test-contract: ## Testes unitários dos contratos
	$(PY) -m pytest tests/contract -x -q

test-e2e: ## Testes ponta a ponta (fluxo completo)
	$(PY) -m pytest tests/e2e -x -q

test-policy: ## Testes da política pr-policy (parser + consistência do workflow inline)
	$(PY) -m pytest scripts/test_pr_policy.py -q

lint: ## Verificação sintática mínima
	$(PY) -m compileall -q contracts services
	@command -v ruff >/dev/null 2>&1 && ruff check contracts services || echo "ruff não instalado (opcional)"

clean: ## Remove artefatos Python (mantém dados)
	rm -rf services/*/__pycache__ contracts/*/__pycache__ .pytest_cache