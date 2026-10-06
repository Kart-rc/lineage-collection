"""Run a real Petclinic JVM+HTTP exercise, preserving traces and response bodies."""
import os, sys, json, time, uuid, hashlib, socket, shutil, subprocess, zipfile
import urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[1]
JAVA=ROOT/'deps/jdk-21.0.12.1+1/bin/java'
MODE=sys.argv[1] if len(sys.argv)>1 else 'enhanced'
DEST=ROOT/'evidence'/MODE
if DEST.exists():
 archive=ROOT/'evidence/archive'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
 archive.mkdir(parents=True);shutil.move(str(DEST),str(archive/MODE))
DEST.mkdir(parents=True,exist_ok=True)
SOURCE='500158f732419217507c7656904b8e6aa1bcc0d6'
with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
jar=next((ROOT/'petclinic/target').glob('spring-petclinic-*.jar'))
args=[str(JAVA),f'-javaagent:{ROOT}/deps/opentelemetry-javaagent.jar',f'-javaagent:{ROOT}/deps/jacoco-agent.jar=destfile={DEST}/jacoco.exec,append=false',
 '-Dotel.service.name=spring-petclinic','-Dotel.resource.attributes=service.namespace=lineage-experiment,service.version='+SOURCE,
 '-Dotel.traces.sampler=always_on','-Dotel.traces.exporter=logging-otlp','-Dotel.metrics.exporter=none','-Dotel.logs.exporter=none',
 '-Dotel.bsp.schedule.delay=200','-Dotel.attribute.value.length.limit=8192',
 '-Dotel.instrumentation.jdbc.statement-sanitizer.enabled=true','-jar',str(jar),
 '--server.address=127.0.0.1','--server.port='+str(port),
 '--spring.datasource.url=jdbc:h2:mem:petclinic;DB_CLOSE_DELAY=-1;DB_CLOSE_ON_EXIT=FALSE',
 '--lineage.experiment.enabled='+('false' if MODE=='baseline' else 'true')]
if MODE=='mutant':
 templates=DEST/'templates';shutil.copytree(ROOT/'petclinic/src/main/resources/templates',templates,dirs_exist_ok=True)
 f=templates/'owners/ownerDetails.html';text=f.read_text();assert "firstName + ' ' + lastName" in text
 f.write_text(text.replace("firstName + ' ' + lastName",'firstName'))
 args+=['--spring.thymeleaf.prefix='+templates.as_uri()+'/','--spring.thymeleaf.cache=false']
(DEST/'command.json').write_text(json.dumps(args,indent=2))
log=(DEST/'service.log').open('w')
p=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT,cwd=ROOT/'petclinic')
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
request=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
active_template=(DEST/'templates/owners/ownerDetails.html') if MODE=='mutant' else ROOT/'petclinic/src/main/resources/templates/owners/ownerDetails.html'
template_bytes=active_template.read_bytes() if MODE=='mutant' else zipfile.ZipFile(jar).read('BOOT-INF/classes/templates/owners/ownerDetails.html')
active_template_sha=hashlib.sha256(template_bytes).hexdigest()
requests=[]
def invoke(path,name):
 trace=uuid.uuid4().hex;parent=uuid.uuid4().hex[:16]
 item={'runId':MODE+':'+name+':'+str(uuid.uuid4()),'scenario':name,'traceId':trace,'parentSpanId':parent,'path':path,
 'activeTemplateSha256':active_template_sha,'sourceCommit':SOURCE,'jarSha256':hashlib.sha256(jar.read_bytes()).hexdigest(),'scheduledAt':datetime.now(timezone.utc).isoformat()}
 req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,headers={'traceparent':f'00-{trace}-{parent}-01','Accept-Language':'en'})
 try:
  with request.open(req,timeout=30) as r:body=r.read();status=r.status;headers=dict(r.headers)
 except urllib.error.HTTPError as e:body=e.read();status=e.code;headers=dict(e.headers)
 item.update(status=status,headers=headers,completedAt=datetime.now(timezone.utc).isoformat())
 (DEST/(name+'.html')).write_bytes(body);requests.append(item)
 print(MODE,name,status,len(body),trace,flush=True)
try:
 deadline=time.time()+120
 while time.time()<deadline:
  if p.poll() is not None:raise RuntimeError('JVM exited during startup')
  # TCP readiness only: no warm-up endpoint polluting business code coverage.
  try:
   with socket.create_connection(('127.0.0.1',port),timeout=.5):break
  except OSError:time.sleep(.5)
 else:raise TimeoutError('Service did not bind within 120 seconds')
 gold=json.loads((ROOT/'gold/owner_details_gold.json').read_text())
 for fixture in gold['fixtures']:invoke(fixture['request']['path'],fixture['name'])
 invoke('/owners/999999','missing_owner')
 time.sleep(2)
finally:
 p.terminate()
 try:p.wait(timeout=20)
 except subprocess.TimeoutExpired:p.kill();p.wait()
 log.close();(DEST/'request-manifest.json').write_text(json.dumps(requests,indent=2))
from telemetry import load_spans
spans=load_spans(DEST/'service.log');(DEST/'spans.json').write_text(json.dumps(spans,indent=2))
print('Captured total spans:',len(spans),flush=True)
