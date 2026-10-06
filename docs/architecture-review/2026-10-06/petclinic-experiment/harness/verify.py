"""Independent comparison of real outputs and adapted edges with frozen gold."""
import json, hashlib, copy, re, sys
from pathlib import Path
from lxml import html
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from adapter import adapt
from telemetry import attributes
ROOT=Path(__file__).resolve().parents[1]
for line in (ROOT/'gold/SHA256SUMS').read_text().splitlines():
 digest,name=line.split(None,1)
 assert hashlib.sha256((ROOT/'gold'/name.strip()).read_bytes()).hexdigest()==digest, 'Frozen gold changed: '+name
GOLD=json.loads((ROOT/'gold/owner_details_gold.json').read_text())
CONTRACT_RAW=(ROOT/'contracts/owner-details-v1.json').read_text().strip()
CONTRACT_SHA=hashlib.sha256(CONTRACT_RAW.encode()).hexdigest()
CUSTOM={"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","properties":{
 "_producer":{"type":"string","format":"uri"},"_schemaURL":{"type":"string","format":"uri"},
 "evidenceClass":{"enum":["DECLARED_AND_EXECUTED","INCOMPLETE","OBSERVED_FAILURE"]},"requiredEvidencePresent":{"type":"boolean"},"captureCompleteness":{"const":"NOT_ESTABLISHED"},
 "traceId":{"type":"string"},"spanIds":{"type":"array","items":{"type":"string"}},
 "jdbcSpanIds":{"type":"array","items":{"type":"string"}},"contractSha256":{"type":"string"},
 "sourceCommit":{"type":"string"},"jarSha256":{"type":"string"},"activeTemplateSha256":{"type":"string"},"missingEvidence":{"type":"array","items":{"type":"string"}},
 "scope":{"type":"string"},"businessOutputValidated":{"const":False}},
 "required":["_producer","_schemaURL","evidenceClass","traceId","spanIds","jdbcSpanIds","contractSha256","sourceCommit","jarSha256","activeTemplateSha256","requiredEvidencePresent","captureCompleteness","missingEvidence","scope","businessOutputValidated"],"additionalProperties":False}
SCHEMA_HASH=hashlib.sha256(json.dumps(CUSTOM,sort_keys=True).encode()).hexdigest()
SCHEMA_URL='urn:lineage-experiment:schema:sha256:'+SCHEMA_HASH
CUSTOM['$id']=SCHEMA_URL
(ROOT/'schemas'/('experiment_lineageEvidence-'+SCHEMA_HASH+'.json')).write_text(json.dumps(CUSTOM,indent=2))
registry=Registry()
for path in (ROOT/'schemas').glob('*.json'):
 data=json.loads(path.read_text())
 registry=registry.with_resource(data['$id'],Resource.from_contents(data))

def validate_schema(event):
 pairs=[(event,event['schemaURL']), (event['run']['facets']['experiment_lineageEvidence'],SCHEMA_URL)]
 for output in event['outputs']:
  for facet in output.get('facets',{}).values():pairs.append((facet,facet['_schemaURL']))
 for value,url in pairs:Draft202012Validator({'$ref':url},registry=registry,format_checker=FormatChecker()).validate(value)

def output_check(path,fixture,manifest):
 tree=html.fromstring(path.read_bytes());tables=tree.xpath('//table[contains(concat(" ", normalize-space(@class), " "), " table-striped ")]')
 cells=tables[0].xpath('./tr/td|./tbody/tr/td') if tables else []
 names=['owner.name','owner.address','owner.city','owner.telephone']
 actual={name:''.join(td.itertext()).strip() for name,td in zip(names,cells)}
 labels=[''.join(th.itertext()).strip() for th in tables[0].xpath('./tr/th|./tbody/tr/th')] if tables else []
 unresolved=any(k in ('th:text','th:object') for node in tables[0].iter() for k in node.attrib) if tables else True
 http_ok=manifest['status']==200 and manifest['headers'].get('Content-Type','').startswith('text/html') and 'Location' not in manifest['headers']
 return {'pass':http_ok and len(cells)==4 and labels==['Name','Address','City','Telephone'] and not unresolved and actual==fixture['rendered_fields'],'actual':actual,'expected':fixture['rendered_fields'],'cellCount':len(cells),'rowLabels':labels,'httpChecksPass':http_ok,'unresolvedThymeleaf':unresolved}

def edges(event):
 value=set();influence=set()
 expected_input={'namespace':'jdbc:h2:mem:petclinic','name':'PUBLIC.OWNERS'}
 expected_output={'namespace':'logical://spring-petclinic','name':'owners/ownerDetails#owner-summary'}
 for output in event['outputs']:
  facet=output.get('facets',{}).get('columnLineage',{})
  output_valid=all(output.get(k)==v for k,v in expected_output.items())
  for target,mapping in facet.get('fields',{}).items():
   for source in mapping['inputFields']:
    for transform in (source.get('transformations') or [{}]):
     description=transform.get('description','')
     kind='CONCAT_HTML_RENDER' if description=="HTML_TEXT(first_name + ' ' + last_name)" else 'HTML_RENDER' if description==f"HTML_TEXT({source['field']})" else 'UNKNOWN'
     if transform.get('type')!='DIRECT' or transform.get('subtype')!='TRANSFORMATION':kind='UNKNOWN'
     identity_valid=output_valid and all(source.get(k)==v for k,v in expected_input.items()) and any(all(i.get(k)==v for k,v in expected_input.items()) for i in event['inputs'])
     value.add(('owners.'+source['field'] if identity_valid else 'UNRESOLVED:'+str(source), 'owner.'+target if output_valid else 'UNRESOLVED:'+str(output.get('name')),kind))
  for source in facet.get('dataset',[]):
   for transform in (source.get('transformations') or [{}]):
    if transform.get('type')=='INDIRECT' and transform.get('subtype')=='FILTER':
     for target in GOLD['scope']['logical_targets']:
      identity_valid=output_valid and all(source.get(k)==v for k,v in expected_input.items())
      influence.add(('owners.'+source['field'] if identity_valid else 'UNRESOLVED:'+str(source),target,'ROW_SELECTION'))
    else:
     for target in GOLD['scope']['logical_targets']:influence.add(('owners.'+source['field'],target,'UNKNOWN'))
 return value,influence

def score(event):
 observed,filtered=edges(event)
 gold={(e['source'],e['target'],e['transformation']) for e in GOLD['value_edges']}
 fg={(e['source'],e['target'],e['transformation']) for e in GOLD['influence_edges']}
 def quality(obs,want):return {'truePositive':len(obs & want),'predicted':len(obs),'gold':len(want),'precision':len(obs & want)/len(obs) if obs else None,'recall':len(obs & want)/len(want) if want else None,'missing':sorted(want-obs),'unexpected':sorted(obs-want)}
 return {'value':quality(observed,gold),'filter':quality(filtered,fg),'pass':observed==gold and filtered==fg}

reports={}
for mode in ('baseline','enhanced','mutant'):
 dest=ROOT/'evidence'/mode
 assert (dest/'spans.json').exists(), 'Missing required evidence mode: '+mode
 spans=json.loads((dest/'spans.json').read_text());manifests=json.loads((dest/'request-manifest.json').read_text())
 reports[mode]=[]
 for manifest in manifests:
  name=manifest['scenario'];ev,diag=adapt(spans,manifest,CONTRACT_SHA,SCHEMA_URL)
  validate_schema(ev)
  # Emit a lifecycle pair with the same stable run ID for a real observed HTTP span.
  events=[]
  server=next((s for s in spans if s['traceId']==manifest['traceId'] and s.get('kind') in (2,'SPAN_KIND_SERVER')),None)
  if server:
   import datetime
   start=copy.deepcopy(ev);start['eventType']='START';start['inputs']=[];start['outputs']=[]
   start['eventTime']=datetime.datetime.fromtimestamp(int(server['startTimeUnixNano'])/1e9,datetime.timezone.utc).isoformat()
   ev['eventType']='FAIL' if manifest['status']>=400 else 'COMPLETE'
   validate_schema(start);events=[start,ev]
  (dest/(name+'-openlineage.json')).write_text(json.dumps(events,indent=2))
  fixture=next((f for f in GOLD['fixtures'] if f['name']==name),None)
  output=output_check(dest/(name+'.html'),fixture,manifest) if fixture else {'pass':manifest['status']==500,'expectedStatus':500,'actualStatus':manifest['status']}
  quality=score(ev) if fixture else None
  reports[mode].append({'scenario':name,'status':manifest['status'],'traceId':manifest['traceId'],'runId':ev['run']['runId'],'output':output,'capture':diag,'typedEdgeQuality':quality,'schemaValid':True})
 if mode=='enhanced':
  primary=manifests[0];base,diag=adapt(spans,primary,CONTRACT_SHA,SCHEMA_URL)
  controls=[]
  for label,mutate in [
   ('drop_mapping_event',lambda ss:[s.update(events=[e for e in s.get('events',[]) if e.get('name')!='experiment.lineage.mapping']) for s in ss]),
   ('drop_all_jdbc_spans',lambda ss:ss.__setitem__(slice(None),[s for s in ss if 'jdbc' not in s.get('scope',{}).get('name','')])),
   ('drop_http_server_span',lambda ss:ss.__setitem__(slice(None),[s for s in ss if s.get('kind') not in (2,'SPAN_KIND_SERVER')]))]:
   sample=copy.deepcopy(spans);mutate(sample);changed,check=adapt(sample,primary,CONTRACT_SHA,SCHEMA_URL)
   controls.append({'control':label,'method':'Offline deletion from actual captured evidence','detected':not check['requiredEvidencePresent'] and not score(changed)['pass'],'capture':check})
  for label in ('omit_last_name_edge','invent_id_value_edge','mislabel_identity','wrong_dataset_identity','untyped_extra_source','wrong_input_identity'):
   changed=copy.deepcopy(base);fields=changed['outputs'][0]['facets']['columnLineage']['fields']
   if label=='omit_last_name_edge':fields['name']['inputFields']=[x for x in fields['name']['inputFields'] if x['field']!='last_name']
   elif label=='invent_id_value_edge':
    invented=copy.deepcopy(fields['name']['inputFields'][0]);invented['field']='id';fields['name']['inputFields'].append(invented)
   elif label=='wrong_dataset_identity':changed['outputs'][0]['name']='other-view'
   elif label=='untyped_extra_source':fields['name']['inputFields'].append({'namespace':'jdbc:h2:mem:petclinic','name':'PUBLIC.OWNERS','field':'id'})
   elif label=='wrong_input_identity':fields['name']['inputFields'][0]['namespace']='jdbc:h2:mem:other-db'
   else:
    for field in fields.values():
     for source in field['inputFields']:
      for t in source['transformations']:t.update(subtype='IDENTITY',description='IDENTITY')
   comparison=score(changed);controls.append({'control':label,'method':'Offline mutation of adapted actual event','detected':not comparison['pass'],'typedEdgeQuality':comparison})
  (dest/'negative-controls.json').write_text(json.dumps(controls,indent=2))
  reports['negativeControls']=controls
(ROOT/'evidence/comparison.json').write_text(json.dumps(reports,indent=2))
print(json.dumps({mode:[{'scenario':r['scenario'],'outputPass':r['output']['pass'],'capture':r['capture'],'typedEdgePass':r['typedEdgeQuality']['pass'] if r['typedEdgeQuality'] else None} for r in results] for mode,results in reports.items() if mode!='negativeControls'},indent=2))
if reports.get('negativeControls'):print('Negative controls detected:',sum(c['detected'] for c in reports['negativeControls']),'/',len(reports['negativeControls']))

expected_names={f['name'] for f in GOLD['fixtures']}|{'missing_owner'}
for mode in ('baseline','enhanced','mutant'):
 assert len(reports[mode])==3 and {r['scenario'] for r in reports[mode]}==expected_names, 'Scenario matrix incomplete'
 for r in reports[mode]:
  positive=r['scenario']!='missing_owner'
  assert r['output']['pass'] == (mode!='mutant' or not positive), f"Unexpected output verdict: {mode} {r['scenario']}"
  if positive:
   assert r['typedEdgeQuality']['pass']==(mode!='baseline'), f"Unexpected lineage verdict: {mode} {r['scenario']}"
   assert r['capture']['requiredEvidencePresent']==(mode!='baseline')
  else:
   assert r['capture']['declarationCount']==0 and r['capture']['requiredEvidencePresent']
assert len(reports['negativeControls'])==9 and all(c['detected'] for c in reports['negativeControls']), 'Negative control escaped'
print('FINAL ACCEPTANCE GATE: PASS')
