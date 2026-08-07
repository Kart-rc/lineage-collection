.PHONY: setup test dev reset build synth package-aws verify

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

synth:
	npm run synth

package-aws:
	npm run package:aws

verify:
	npm run verify
