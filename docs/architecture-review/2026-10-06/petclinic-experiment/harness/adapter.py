"""Bounded OTel→OpenLineage semantic adapter. Never infers fields from adjacency."""
import json,hashlib,uuid,datetime
from telemetry import attributes
PRODUCER='urn:lineage-experiment:otel-openlineage-adapter:v1'
EVENT_SCHEMA='https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent'
COLUMN_SCHEMA='https://openlineage.io/spec/facets/1-2-0/ColumnLineageDatasetFacet.json#/$defs/ColumnLineageDatasetFacet'
SCHEMA_SCHEMA='https://openlineage.io/spec/facets/1-2-0/SchemaDatasetFacet.json#/$defs/SchemaDatasetFacet'

def facet(schema,**fields):return {'_producer':PRODUCER,'_schemaURL':schema,**fields}
def adapt(spans,manifest,contract_sha,schema_url):
 trace=[s for s in spans if s['traceId'].lower()==manifest['traceId'].lower()]
 servers=[s for s in trace if s.get('kind') in (2,'SPAN_KIND_SERVER')]
 success=manifest['status']<400
 expected_declarations=1 if success else 0
 jdbc=[s for s in trace if 'jdbc' in s.get('scope',{}).get('name','')]
 selects=[]
 for s in jdbc:
  a=attributes(s.get('attributes',[]));q=a.get('db.query.text',a.get('db.statement','')).lower()
  if re_select_owner(q):selects.append(s)
 declarations=[]
 for s in trace:
  for e in s.get('events',[]):
   if e.get('name')=='experiment.lineage.mapping':declarations.append((s,e,attributes(e.get('attributes',[]))))
 reasons=[]
 if len(servers)!=1:reasons.append('Expected exactly one HTTP SERVER span')
 if not selects:reasons.append('No observed JDBC owners SELECT in scenario trace')
 if len(declarations)!=expected_declarations:reasons.append(f'Expected {expected_declarations} successful mapping declarations')
 by_id={s['spanId']:s for s in trace}
 if len(by_id)!=len(trace):reasons.append('Duplicate span IDs')
 if len(servers)==1:
  root=servers[0]
  if root.get('parentSpanId')!=manifest['parentSpanId']:reasons.append('HTTP parent differs from scheduled request context')
  if root.get('resource',{}).get('service.name')!='spring-petclinic' or root.get('resource',{}).get('service.version')!=manifest['sourceCommit']:reasons.append('Service/build identity mismatch')
  a=attributes(root.get('attributes',[]))
  if a.get('http.route')!='/owners/{ownerId}' or a.get('http.request.method')!='GET' or a.get('url.path')!=manifest['path']:reasons.append('Unexpected HTTP operation/path')
  if int(a.get('http.response.status_code',0))!=manifest['status']:reasons.append('HTTP status differs from actual response')
  def descendant(s):
   for _ in range(len(trace)+1):
    if s['spanId']==root['spanId']:return True
    s=by_id.get(s.get('parentSpanId'))
    if s is None:return False
   return False
  if any(not descendant(s) for s in selects):reasons.append('JDBC evidence has no complete parent chain to HTTP server')
  if any(not descendant(s) for s,_,_ in declarations):reasons.append('Declaration has no parent chain to HTTP server')
 for s in trace:
  if any(int(s.get(k,0))>0 for k in ('droppedAttributesCount','droppedEventsCount','droppedLinksCount')):reasons.append('OTel reports dropped span content')
 for s in selects:
  a=attributes(s.get('attributes',[]))
  if s.get('status',{}).get('code') in (2,'STATUS_CODE_ERROR'):reasons.append('JDBC operation reported error')
  if a.get('db.system')!='h2' or a.get('db.name')!='petclinic':reasons.append('Database identity mismatch')
 fields={}; influences=[]; contract=None
 if len(declarations)==1:
  s,e,a=declarations[0]
  raw=a.get('lineage.mapping.json','')
  try:contract=json.loads(raw)
  except ValueError:reasons.append('Malformed mapping JSON')
  if hashlib.sha256(raw.encode()).hexdigest()!=contract_sha or a.get('lineage.contract.sha256')!=contract_sha:
   reasons.append('Mapping contract digest mismatch')
  if a.get('lineage.operation')!='GET /owners/{ownerId}':reasons.append('Declaration operation mismatch')
  if a.get('lineage.evidence.class')!='DECLARED_AND_EXECUTED' or a.get('lineage.mapping.success') is not True:
   reasons.append('Unsupported evidence class or unsuccessful operation')
  if contract and not reasons:
   for edge in contract['valueEdges']:
    fields.setdefault(edge['output'],{'inputFields':[]})['inputFields'].append({
     **contract['input'],'field':edge['input'],'transformations':[{
      'type':edge['type'],'subtype':edge['subtype'],'description':edge['description'],'masking':False}]})
   for edge in contract['datasetInfluences']:
    influences.append({**contract['input'],'field':edge['input'],'transformations':[{
     'type':edge['type'],'subtype':edge['subtype'],'description':edge['description'],'masking':False}]})
 complete=not reasons
 span=servers[0] if servers else {}
 ns=int(span.get('endTimeUnixNano',0))
 when=datetime.datetime.fromtimestamp(ns/1e9,tz=datetime.timezone.utc).isoformat() if ns else manifest['scheduledAt']
 status=attributes(span.get('attributes',[])).get('http.response.status_code',attributes(span.get('attributes',[])).get('http.status_code'))
 evidence=facet(schema_url,evidenceClass='DECLARED_AND_EXECUTED' if complete and success else 'OBSERVED_FAILURE' if complete else 'INCOMPLETE',
  traceId=manifest['traceId'],spanIds=[s['spanId'] for s in trace],jdbcSpanIds=[s['spanId'] for s in selects],
  contractSha256=contract_sha,sourceCommit=manifest['sourceCommit'],jarSha256=manifest['jarSha256'],activeTemplateSha256=manifest['activeTemplateSha256'],requiredEvidencePresent=complete,captureCompleteness='NOT_ESTABLISHED',
  missingEvidence=reasons,scope='Owner summary table only',businessOutputValidated=False)
 event={'eventTime':when,'eventType':'COMPLETE' if complete and int(status or 0)<400 else 'OTHER',
  'producer':PRODUCER,'schemaURL':EVENT_SCHEMA,
  'run':{'runId':str(uuid.uuid5(uuid.NAMESPACE_URL,manifest['runId'])), 'facets':{'experiment_lineageEvidence':evidence}},
  'job':{'namespace':'service://spring-petclinic','name':'GET /owners/{ownerId}','facets':{}},'inputs':[],'outputs':[]}
 if complete and contract and success:
  event['inputs']=[{**contract['input'],'facets':{}}]
  event['outputs']=[{**contract['output'],'facets':{
   'schema':facet(SCHEMA_SCHEMA,fields=[{'name':f,'type':'HTML_TEXT'} for f in fields]),
   'columnLineage':facet(COLUMN_SCHEMA,fields=fields,dataset=influences)}}]
 return event,{'traceSpanCount':len(trace),'httpServerSpanCount':len(servers),'jdbcSpanCount':len(jdbc),
  'ownersSelectSpanCount':len(selects),'declarationCount':len(declarations),'requiredEvidencePresent':complete,'reasons':reasons}

def re_select_owner(sql):
 import re
 return bool(re.search(r'\bselect\b',sql) and re.search(r'\bfrom\s+(?:[\w".]+\.)?owners\b',sql,re.I))
