"""Create a small, sanitized, reproducible evidence bundle, excluding tools/caches."""
from pathlib import Path
import json,hashlib,shutil,zipfile,platform,datetime,re
ROOT=Path(__file__).resolve().parents[1];DEST=ROOT/'deliverable'
if DEST.exists():shutil.rmtree(DEST)
DEST.mkdir()
records=[]
def save(relative,data,original=None):
 p=DEST/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
 records.append({'path':str(relative),'originalSha256':hashlib.sha256(original if original is not None else data).hexdigest(),'deliveredSha256':hashlib.sha256(data).hexdigest()})
def copy(relative):
 raw=(ROOT/relative).read_bytes()
 # Execution-root path normalization, never business-data modification.
 data=raw.replace(str(ROOT).encode(),b'${EXPERIMENT_ROOT}')
 # JaCoCo defaults its session ID from the host name; retain a neutral label.
 if str(relative)=='evidence/enhanced/jacoco.xml':
  data=re.sub(rb'(<sessioninfo\s+id=")[^"]*(")',rb'\1petclinic-enhanced-experiment\2',data)
 save(relative,data,raw)
for f in ['README.md','LICENSE-upstream.txt','LICENSE-OpenLineage.txt','NOTICE-OpenLineage.txt','THIRD-PARTY-NOTICES.md','requirements.txt']:
 copy(Path(f))
for directory in ['harness','contracts','gold']:
 for p in (ROOT/directory).rglob('*'):
  if p.is_file() and '__pycache__' not in p.parts:copy(p.relative_to(ROOT))
used=json.loads((ROOT/'evidence/enhanced/owner_1_seed-openlineage.json').read_text())[1]['run']['facets']['experiment_lineageEvidence']['_schemaURL'].split(':')[-1]
for p in (ROOT/'schemas').glob('*.json'):
 if not p.name.startswith('experiment_') or used in p.name:copy(p.relative_to(ROOT))
for mode in ['baseline','enhanced','mutant']:
 d=ROOT/'evidence'/mode
 for p in d.iterdir():
  if p.suffix in ['.json','.html','.xml'] and p.name!='spans.json':copy(p.relative_to(ROOT))
 raw=(d/'spans.json').read_bytes();spans=json.loads(raw)
 for s in spans:
  s['resource']={k:v for k,v in s.get('resource',{}).items() if not k.startswith(('host.','process.'))}
 data=json.dumps(spans,indent=2).encode().replace(str(ROOT).encode(),b'${EXPERIMENT_ROOT}')
 save(Path('evidence')/mode/'spans.json',data,raw)
for name in ['comparison.json','verification-console.log','template-provenance-verification.json','build.log','upstream-focused-tests.log']:
 copy(Path('evidence')/name)
for name in ['env-manifest.json','coverage-denominators.json','independent-review.md']:
 if (ROOT/'evidence'/name).exists():copy(Path('evidence')/name)
sanitize={'policy':['Removed host.* and process.* OTel resource attributes','Replaced hostname-derived JaCoCo session ID with a neutral experiment label','Replaced execution-root path with ${EXPERIMENT_ROOT} in text artifacts','Kept actual trace IDs, span IDs, routes, status, SQL and event metadata','Kept synthetic public Petclinic fixtures and actual response bodies','Excluded downloaded binaries, source checkout, dependency cache, earlier runs, unfiltered service logs and initial long error log'], 'files':records}
(DEST/'SANITIZATION.json').write_text(json.dumps(sanitize,indent=2))
checks=[]
for p in sorted(DEST.rglob('*')):
 if p.is_file():checks.append(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(DEST)))
(DEST/'SHA256SUMS').write_text('\n'.join(checks)+'\n')
zip_path=ROOT/'Petclinic-OTel-Lineage-Experiment.zip'
with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as z:
 for p in sorted(DEST.rglob('*')):
  if p.is_file():z.write(p,'Petclinic-OTel-Lineage-Experiment/'+str(p.relative_to(DEST)))
print(zip_path,zip_path.stat().st_size,'bytes')
