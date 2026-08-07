.PHONY: setup test dev reset build synth package-aws workflow-export workflow-check aws-deploy aws-smoke aws-cleanup acceptance-smoke verify

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

workflow-export:
	npm run workflow:export

workflow-check:
	npm run workflow:check

aws-deploy:
	./scripts/deploy_ephemeral_aws.sh

aws-smoke:
	./scripts/smoke_ephemeral_aws.sh

aws-cleanup:
	./scripts/cleanup_ephemeral_aws.sh

acceptance-smoke:
	npm run acceptance:smoke

verify:
	npm run verify
