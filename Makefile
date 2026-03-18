SHELL := /bin/sh

.PHONY: up down test smoke

up:
	mkdir -p data data/ollama config config/personas
	if [ -z "$$(ls -A config/personas 2>/dev/null)" ]; then cp -r personas/. config/personas/; fi
	docker compose up --build -d

down:
	docker compose down

test:
	docker compose run --rm api pytest -q

smoke:
	curl -fsS http://localhost:8000/health
	curl -fsS http://localhost:8000/v1/attractors?window=5
