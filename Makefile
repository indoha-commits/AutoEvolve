.PHONY: setup setup-core setup-engines init doctor dev test lint check email-worker-install

setup: setup-core setup-engines init doctor

setup-core:
	python3 -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -e ".[dev]"

setup-engines:
	./scripts/install_engines.sh

init:
	./scripts/init_env.sh

doctor:
	.venv/bin/python scripts/doctor.py

dev:
	.venv/bin/python -m uvicorn app.api:app --reload --host 0.0.0.0 --port 8787

test:
	.venv/bin/python -m pytest

lint:
	.venv/bin/python -m ruff check app core services agents tools tests

check: lint test doctor

email-worker-install:
	npm --prefix company-sales-email-ingress ci
