.PHONY: setup test dev reset build verify

setup:
	uv sync --project apps/api --extra dev
	npm ci

test:
	npm test

dev:
	npm run dev

reset:
	npm run reset

build:
	npm run build

verify:
	npm run verify
