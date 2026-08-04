.PHONY: setup test dev build

setup:
	uv sync --project apps/api --extra dev
	npm install

test:
	uv run --project apps/api pytest
	npm test

dev:
	npm run dev

build:
	npm run build
