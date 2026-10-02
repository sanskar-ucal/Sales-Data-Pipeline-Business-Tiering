PY ?= python
DBT ?= dbt
DBT_FLAGS = --project-dir dbt --profiles-dir dbt

.PHONY: install data load validate-raw dbt-build validate-marts tier dbt-post monitor publish run-all docs test lint up down

install:
	$(PY) -m pip install -e ".[dev]"
	$(PY) -m pip install -r requirements-dbt.txt

data:
	$(PY) -m sales_pipeline generate-data

load:
	$(PY) -m sales_pipeline load

validate-raw:
	$(PY) -m sales_pipeline validate raw

dbt-build:
	$(DBT) deps $(DBT_FLAGS)
	$(DBT) build --exclude tag:post_tiering $(DBT_FLAGS)

validate-marts:
	$(PY) -m sales_pipeline validate marts

tier:
	$(PY) -m sales_pipeline tier
	$(PY) -m sales_pipeline validate tiering

dbt-post:
	$(DBT) build --select tag:post_tiering $(DBT_FLAGS)

monitor:
	$(PY) -m sales_pipeline monitor

publish:
	$(PY) -m sales_pipeline publish

run-all: load validate-raw dbt-build validate-marts tier dbt-post monitor publish

docs:
	$(DBT) docs generate $(DBT_FLAGS)
	$(DBT) docs serve $(DBT_FLAGS)

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check src tests airflow

up:
	cp -n .env.example .env || true
	docker compose up -d --build

down:
	docker compose down
