"""Download pinned official tools and the official source archive. No remote writes."""
import os,hashlib,tarfile,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
# Artifact hashes verified during the recorded experiment.
ITEMS={
 'petclinic.tar.gz':('https://codeload.github.com/spring-projects/spring-petclinic/tar.gz/500158f732419217507c7656904b8e6aa1bcc0d6','4932ae0209375b3cca3dd7c8f6f9345498d75dedc5970dcfa91814f1b830768d'),
 'deps/jdk.tar.gz':('https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/OpenJDK21U-jdk_x64_linux_hotspot_21.0.12.1_1.tar.gz','ce79869e1307ed8ee1e2baa86a412b1eb5b75d10a01006d788a6f968bcfaee94'),
 'deps/maven.tar.gz':('https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.9.16/apache-maven-3.9.16-bin.tar.gz','80ffca22aed9e8b9713a232f3394fd81d7f20322df75efdb2b047dbd3e3a23bb'),
 'deps/opentelemetry-javaagent.jar':('https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v2.32.0/opentelemetry-javaagent.jar','f787eb6c7f3d18e69a431e108a15278d25ee37f83d68b678f621e063f3988f82'),
}
def download(name,url,digest):
 path=ROOT/name;path.parent.mkdir(parents=True,exist_ok=True)
 if not path.exists():
  print('Downloading',name,flush=True)
  with urllib.request.urlopen(url,timeout=180) as r,path.open('wb') as f:
   while data:=r.read(1024*1024):f.write(data)
 if digest and hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise RuntimeError('Checksum mismatch: '+name)
 return path
for name,(url,digest) in ITEMS.items():download(name,url,digest)
for name,directory in [('deps/jdk.tar.gz','deps'),('deps/maven.tar.gz','deps')]:
 with tarfile.open(ROOT/name) as t:t.extractall(ROOT/directory,filter='data')
if not (ROOT/'petclinic').exists():
 with tarfile.open(ROOT/'petclinic.tar.gz') as t:t.extractall(ROOT,filter='data')
 (ROOT/'spring-petclinic-500158f732419217507c7656904b8e6aa1bcc0d6').rename(ROOT/'petclinic')
for artifact,filename in [('org.jacoco.agent','jacoco-agent.jar'),('org.jacoco.cli','jacoco-cli.jar')]:
 suffix='runtime' if artifact.endswith('agent') else 'nodeps'
 url=f'https://repo.maven.apache.org/maven2/org/jacoco/{artifact}/0.8.15/{artifact}-0.8.15-{suffix}.jar'
 digest={'jacoco-agent.jar':'fb5b0036a0899ea97edfa0fc2c7985b55f3f7c5695028163e5e60d4f3cf6075d','jacoco-cli.jar':'d2b74b20b415163c1f53261e7c5ef4445b788327094208ff821e0c2baf9bc8f1'}[filename]
 download('deps/'+filename,url,digest)
print('Pinned source and tools ready. Run instrument.py, then build.sh.')
