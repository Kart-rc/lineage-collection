#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 harness/run_http.py baseline
python3 harness/run_http.py enhanced
python3 harness/run_http.py mutant
python3 harness/verify.py | tee evidence/verification-console.log
deps/jdk-21.0.12.1+1/bin/java -jar deps/jacoco-cli.jar report evidence/enhanced/jacoco.exec \
 --classfiles petclinic/target/classes --sourcefiles petclinic/src/main/java --xml evidence/enhanced/jacoco.xml
