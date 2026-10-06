#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export JAVA_HOME="$ROOT/deps/jdk-21.0.12.1+1"
# Reuse the host's established Java trust store if present. Do not disable TLS checks.
if [[ -f /etc/ssl/certs/java/cacerts ]]; then
  export MAVEN_OPTS="${MAVEN_OPTS:-} -Djavax.net.ssl.trustStore=/etc/ssl/certs/java/cacerts"
fi
python3 - <<'PY'
import os,urllib.parse,pathlib,html
root=pathlib.Path.cwd();proxy=os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY');p=''
if proxy:
 u=urllib.parse.urlparse(proxy)
 p=f'<proxies><proxy><id>environment</id><active>true</active><protocol>http</protocol><host>{html.escape(u.hostname)}</host><port>{u.port or 80}</port><nonProxyHosts>localhost|127.0.0.1</nonProxyHosts></proxy></proxies>'
(root/'deps/maven-settings.xml').write_text('<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0"><localRepository>'+str(root/'deps/m2')+'</localRepository>'+p+'</settings>')
PY
mkdir -p evidence
"$ROOT/deps/apache-maven-3.9.16/bin/mvn" -s deps/maven-settings.xml -f petclinic/pom.xml -B -ntp \
 -Dmaven.test.skip=true -Dspring-javaformat.skip=true -Dcheckstyle.skip=true -Dmaven.gitcommitid.skip=true package | tee evidence/build.log
