"""Construct a manually adjudicated source-derived oracle, never read instrumentation."""
from pathlib import Path
import json
BASE = Path(__file__).resolve().parent
REPO = 'spring-projects/spring-petclinic'
PIN = '500158f732419217507c7656904b8e6aa1bcc0d6'
PATHS = {
 'controller': 'src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java',
 'repository': 'src/main/java/org/springframework/samples/petclinic/owner/OwnerRepository.java',
 'owner': 'src/main/java/org/springframework/samples/petclinic/owner/Owner.java',
 'person': 'src/main/java/org/springframework/samples/petclinic/model/Person.java',
 'entity': 'src/main/java/org/springframework/samples/petclinic/model/BaseEntity.java',
 'template': 'src/main/resources/templates/owners/ownerDetails.html',
 'schema': 'src/main/resources/db/h2/schema.sql',
 'data': 'src/main/resources/db/h2/data.sql',
 'config': 'src/main/resources/application.properties',
 'locale': 'src/main/java/org/springframework/samples/petclinic/system/WebConfiguration.java',
}
def ref(which, lo, hi=None):
 return {'file': PATHS[which], 'lines': [lo, hi or lo], 'url': f'https://github.com/{REPO}/blob/{PIN}/{PATHS[which]}#L{lo}' + (f'-L{hi}' if hi else '')}
fields = [
 ('first_name','firstName','owner.name','Name',20,'CONCAT_HTML_RENDER', [ref('person',31,46)]),
 ('last_name','lastName','owner.name','Name',20,'CONCAT_HTML_RENDER', [ref('person',36,54)]),
 ('address','address','owner.address','Address',24,'HTML_RENDER', [ref('owner',52,55),ref('owner',72,77)]),
 ('city','city','owner.city','City',28,'HTML_RENDER', [ref('owner',57,60),ref('owner',80,85)]),
 ('telephone','telephone','owner.telephone','Telephone',32,'HTML_RENDER', [ref('owner',62,65),ref('owner',88,93)]),
]
value_edges=[]
for column, prop, sink,label,line,kind,refs in fields:
 value_edges.append({
  'source':f'owners.{column}', 'target':sink, 'dependency':'VALUE',
  'transformation': kind,
  'steps':['ORM_COLUMN_TO_PROPERTY']+(['CONCAT_WITH_SPACE'] if sink=='owner.name' else [])+['THYMELEAF_ESCAPED_TEXT_RENDER'],
  'property':f'owner.{prop}',
  'render_expression': "firstName + ' ' + lastName" if sink=='owner.name' else prop,
  'evidence':refs+[ref('config',12), ref('controller',178,185), ref('template',17,line)]
 })
filter_edges=[{'source':'owners.id','target':f'owner.{f}','dependency':'FILTER','transformation':'ROW_SELECTION','predicate':'owners.id = request.path.ownerId','value_lineage':False,'evidence':[ref('entity',35,40),ref('controller',64,69),ref('controller',178,184),ref('repository',47,60)]} for f in ['name','address','city','telephone']]
fixtures=[
 {'name':'owner_1_seed','request':{'method':'GET','path':'/owners/1'},'source_row':{'id':1,'first_name':'George','last_name':'Franklin','address':'110 W. Liberty St.','city':'Madison','telephone':'6085551023'},'rendered_fields':{'owner.name':'George Franklin','owner.address':'110 W. Liberty St.','owner.city':'Madison','owner.telephone':'6085551023'},'evidence':[ref('schema',36,43),ref('data',25)]},
 {'name':'owner_2_filter_control','request':{'method':'GET','path':'/owners/2'},'source_row':{'id':2,'first_name':'Betty','last_name':'Davis','address':'638 Cardinal Ave.','city':'Sun Prairie','telephone':'6085551749'},'rendered_fields':{'owner.name':'Betty Davis','owner.address':'638 Cardinal Ave.','owner.city':'Sun Prairie','owner.telephone':'6085551749'},'evidence':[ref('schema',36,43),ref('data',26)]}
]
gold={
 'schema_version':'independent-petclinic-gold/1',
 'oracle_type':'manual_source_adjudication',
 'provenance':{'repository':REPO,'commit':PIN,'archive_sha256':'4932ae0209375b3cca3dd7c8f6f9345498d75dedc5970dcfa91814f1b830768d','source_bytes_verified_by':'pinned_source_manifest.json','independence':'Authored from pinned upstream source, H2 schema and seed data before inspecting instrumentation, extractor, adapter, traces, or predictions.'},
 'scope':{
  'endpoint':'GET /owners/{ownerId}', 'primary_case':'GET /owners/1',
  'database':'H2 freshly initialized from pinned schema.sql and data.sql',
  'target_view':'owners/ownerDetails', 'transport':'real HTTP response body, media type text/html',
  'logical_target_namespace':'owner',
  'logical_targets':['owner.name','owner.address','owner.city','owner.telephone'],
  'source_columns':['owners.first_name','owners.last_name','owners.address','owners.city','owners.telephone'],
  'influence_columns':['owners.id'],
  'out_of_scope':['Pets, pet types, visits and their rendered fields','owner.id-derived edit/add links','Navigation, localization messages, static layout, scripts, status flash messages','Other endpoints including owner creation and update','Hidden internal model fields which are not rendered','Physical byte-range character taint'],
  'boundary_note':'The target is the HTML-rendered owner-summary field. ORM hydration/getters can preserve string values, but the source-to-rendered-sink edge is not IDENTITY. DOM text equality alone does not remove the rendering operation.',
  'not_claimed':'This is a four-target endpoint slice, not whole-response or application-wide lineage.'
 },
 'taxonomy':{
  'VALUE':'Source column contributes to the rendered field value.',
  'FILTER':'Source key participates in selecting the row whose fields are rendered; it is not a contributor to these field values.',
  'HTML_RENDER':'Thymeleaf th:text serializes escaped text into an HTML element.',
  'CONCAT_HTML_RENDER':'firstName + one U+0020 space + lastName, then Thymeleaf escaped text rendering.',
  'IDENTITY':'No transformation at the chosen boundary. Not a valid end-to-end classification for any of the five edges here.',
  'escaping_documentation':'https://www.thymeleaf.org/doc/tutorials/3.1/usingthymeleaf.html#unescaped-text'
 },
 'value_edges':value_edges, 'influence_edges':filter_edges,
 'expected_counts':{'value_edges':5,'filter_edges':4,'logical_targets':4},
 'target_selectors':[
  {'target':'owner.name','row_label':'Name','row_index_1based':1,'cell':'td b','template_line':20},
  {'target':'owner.address','row_label':'Address','row_index_1based':2,'cell':'td','template_line':24},
  {'target':'owner.city','row_label':'City','row_index_1based':3,'cell':'td','template_line':28},
  {'target':'owner.telephone','row_label':'Telephone','row_index_1based':4,'cell':'td','template_line':32}
 ],
 'fixtures':fixtures,
 'public_http_assertions':[
  'Launch the actual pinned application against a fresh H2 database and issue HTTP GET, not a direct controller call or mocked repository.',
  'Use a fresh session with no locale cookie or language-changing query. WebConfiguration defaults the session locale to English.',
  'Assert status 200, no redirect, Content-Type text/html and no server error page.',
  'Within the first owner-summary table, assert exactly four direct summary rows in order Name, Address, City, Telephone; parse DOM text after entity decoding.',
  'Assert field-specific exact fixture values. Global response contains() is insufficient because unrelated pets or navigation can contain coincident values.',
  'Name is one combined rendered target; do not invent separately rendered firstName and lastName fields.',
  'Assert no unresolved Thymeleaf th:text/th:object bindings remain in the owner-summary table.',
  'Retain the raw HTTP response and parsed field extraction as separate evidence.',
  'Perform owner_2_filter_control as an additional HTTP request to rule out hardcoded owner_1 output and demonstrate row-selection behavior.',
  'Passing public output assertions validates behavior only, not the completeness or correctness of emitted lineage edges.'
 ],
 'negative_expectations':[
  {'source':'owners.id','targets':['owner.name','owner.address','owner.city','owner.telephone'],'forbidden_dependency':'VALUE','reason':'Used as selection key; ID-derived hrefs are outside this slice.'},
  {'source':'owners.city','target':'owner.address','forbidden_dependency':'VALUE'},
  {'source':'owners.address','target':'owner.city','forbidden_dependency':'VALUE'},
  {'all_value_edges':'forbid IDENTITY end-to-end transformation'},
  {'logical_targets':'No owner.firstName or owner.lastName separately rendered sinks in this page.'}
 ],
 'mutation_tests':[
  {'id':'omit_last_name_edge','type':'lineage-output mutation','change':'Drop owners.last_name -> owner.name while keeping public HTML unchanged.','expected':'Exact value-edge comparison fails: one false negative.'},
  {'id':'add_id_value_edge','type':'lineage-output mutation','change':'Add owners.id -> owner.name as VALUE instead of only FILTER.','expected':'Precision fails: one false positive. Correct filter edges do not repair it.'},
  {'id':'misclassify_render_identity','type':'lineage-output mutation','change':'Keep source-target pairs, change HTML_RENDER or CONCAT_HTML_RENDER to IDENTITY.','expected':'Topology-only metrics may pass; transform-aware comparison must fail.'},
  {'id':'swap_city_address','type':'application/template mutation','change':'Swap the address and city expressions in the actual template.','expected':'HTTP fixture assertions fail in two fields; unchanged gold is not regenerated.'},
  {'id':'drop_last_name_template','type':'application/template mutation','change':'Render firstName only in Name.','expected':'HTTP Name assertion fails.'},
  {'id':'hardcode_owner_id','type':'application/controller mutation','change':'Always retrieve owner 1 regardless of path ownerId.','expected':'Owner 1 alone can pass; owner_2_filter_control fails.'},
  {'id':'unescaped_html','type':'application/template mutation plus explicit extension fixture','change':'Change th:text to th:utext. Seed fixtures alone cannot distinguish this; use a separate owner with a harmless literal tag and ampersand in address.','expected':'With a controlled extension fixture address A & <em>Probe</em>, correct DOM text is exactly that string and no em child is created; the mutation creates markup and fails. This optional extension is not claimed as executed.'},
  {'id':'duplicate_event','type':'trace normalization control','change':'Repeat an identical edge event.','expected':'Deduplicated graph metrics remain stable; preserve raw event count separately rather than inflating true positives.'}
 ],
 'evaluation':{
  'score_separately':['value source-target topology','transformation correctness','filter influence correctness','public HTTP behavior'],
  'normalization':'Resolve source aliases/table names and rendered-target identifiers through the predeclared mapping, then compare sets. Do not use fixture values or observed trace fields to fabricate edges.',
  'duplicates':'Repository calls or repeated getter/template reads can yield duplicate events; graph scores deduplicate exact semantic edges.',
  'scope_filtering':'Only predeclared targets are scored. Do not silently prune an incorrect source on an in-scope target; that is a false positive.',
  'independence':'Freeze this file and record SHA-256 before extractor results are read. A schema conversion must preserve facts and record the mapping; do not tune gold to predictions.',
  'coverage_limit':'Static source plus two benign fixtures does not prove sensitivity to all runtime values or every framework behavior.'
 },
 'execution_status':'Source-derived oracle and test design only. No Petclinic server or HTTP assertions were executed by the independent gold author.'
}
(BASE/'owner_details_gold.json').write_text(json.dumps(gold,indent=2)+'\n')
print('Wrote independent gold with',len(value_edges),'value edges and',len(filter_edges),'filter edges')
