"""Extract actual OTLP JSON records from the official Java agent logging exporter."""
import json, re

def attributes(items):
 def val(v):
  for kind in ('stringValue','boolValue','intValue','doubleValue','bytesValue'):
   if kind in v:return v[kind]
  if 'arrayValue' in v:return [val(x) for x in v['arrayValue'].get('values',[])]
  return v
 return {i['key']:val(i['value']) for i in items}

def load_spans(path):
 spans=[]
 for line in path.read_text(errors='replace').splitlines():
  pos=line.find('{"resourceSpans"')
  if pos<0:pos=line.find('{"resource"')
  if pos<0:continue
  try: packet=json.JSONDecoder().raw_decode(line[pos:])[0]
  except json.JSONDecodeError:continue
  for rs in packet.get('resourceSpans',[packet] if 'resource' in packet else []):
   for ss in rs.get('scopeSpans',[]):
    for s in ss.get('spans',[]):
     spans.append({**s,'resource':attributes(rs.get('resource',{}).get('attributes',[])), 'scope':ss.get('scope',{})})
 return spans
