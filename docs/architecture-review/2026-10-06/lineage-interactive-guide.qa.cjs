/* Offline regression checks. Run from the repository root after npm ci:
   node path/to/lineage-interactive-guide.qa.cjs path/to/lineage-interactive-guide.html
   Uses the repository's existing jsdom development dependency; no network calls. */
const fs=require('node:fs');const path=require('node:path');const assert=require('node:assert/strict');const {createRequire}=require('node:module');
const rootRequire=createRequire(path.join(process.cwd(),'package.json'));const {JSDOM,VirtualConsole}=rootRequire('jsdom');
const file=path.resolve(process.argv[2]||path.join(__dirname,'lineage-interactive-guide.html'));const html=fs.readFileSync(file,'utf8');
const errors=[];const consoleBridge=new VirtualConsole();consoleBridge.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(html,{runScripts:'dangerously',url:'file://'+file,pretendToBeVisual:true,virtualConsole:consoleBridge});
const w=dom.window,d=w.document,$=id=>d.getElementById(id),q=s=>d.querySelector(s),qa=s=>[...d.querySelectorAll(s)];
let assertions=0;const check=(condition,message)=>{assert.ok(condition,message);assertions++};const eq=(actual,expected,message)=>{assert.equal(actual,expected,message);assertions++};
const click=s=>{const e=typeof s==='string'?q(s):s;assert.ok(e,'missing click target '+s);e.click()};const input=(id,value)=>{$(id).value=value;$(id).dispatchEvent(new w.Event('input',{bubbles:true}))};
const change=(e,checked)=>{e.checked=checked;e.dispatchEvent(new w.Event('change',{bubbles:true}))};
const results=()=>qa('#capacity-results .result-box').map(x=>x.textContent);const categories=[];
// Structural/offline/accessibility foundations.
eq(errors.length,0,'script and CSS initialization error-free');eq(d.documentElement.lang,'en','language');eq(qa('main').length,1,'single main landmark');eq(qa('h1').length,1,'single h1');eq(qa('main>section').length,9,'nine sections');
const ids=qa('[id]').map(e=>e.id);eq(new Set(ids).size,ids.length,'unique ids');for(const a of qa('a[href^="#"]'))check(!!$(a.hash.slice(1)),'valid section anchor '+a.hash);
eq(qa('script[src],link[rel="stylesheet"],iframe,form').length,0,'no remote resources or forms');check(!/\b(fetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon\s*\(|localStorage|sessionStorage)\b/.test(html),'no network/storage code');check(q('meta[http-equiv="Content-Security-Policy"]').content.includes("connect-src 'none'"),'network disabled in CSP');
for(const e of qa('input[id],select[id]'))check(!!q('label[for="'+e.id+'"]')||!!e.closest('label'),'input accessible label '+e.id);
for(const a of qa('a[target="_blank"]'))check(a.rel.includes('noopener')&&a.rel.includes('noreferrer'),'safe external link relationship');
for(const a of qa('a[href]'))check(a.hash||a.href.startsWith('https:'),'safe source protocol');
categories.push('offline/DOM/accessibility foundations');
// All architecture states, repeated to catch stale selection.
for(let repeat=0;repeat<3;repeat++)for(const mode of ['local','aws','target']){click('[data-arch="'+mode+'"]');eq(qa('.arch-node').length,6,'architecture stage count');eq(q('[data-arch="'+mode+'"]').getAttribute('aria-pressed'),'true','architecture selected');for(let i=0;i<6;i++){click('[data-node="'+i+'"]');check($('arch-detail').textContent.length>100,'detail populated');eq(qa('.arch-node[aria-pressed="true"]').length,1,'exactly one architecture stage active')}}
categories.push('54 architecture node selections across three repeated cycles');
// Full trace, both dependency filters, boundary navigation.
click('#step-reset');eq($('step-prev').disabled,true,'first back disabled');for(let i=0;i<7;i++){click('[data-step="'+i+'"]');eq($('step-label').textContent,'Step '+(i+1)+' of 7','step label');check($('step-code').textContent.length>40,'code example');click('[data-rel="value"]');eq(qa('#field-rows .control').length,0,'value filter');check(qa('#field-rows .field-row').length>0,'value edges present');click('[data-rel="all"]');check(qa('#field-rows .field-row').length>0,'all edges present')}eq($('step-next').disabled,true,'last next disabled');click('#step-next');eq($('step-label').textContent,'Step 7 of 7','disabled next stable');for(let i=0;i<6;i++)click('#step-prev');eq($('step-label').textContent,'Step 1 of 7','back chain');for(let i=0;i<6;i++)click('#step-next');eq($('step-label').textContent,'Step 7 of 7','next chain');categories.push('all seven trace steps, both relation views, repeated/back/next boundaries');
// Every combination of the five evidence filters (six cards).
const filters=qa('[data-evidence]');eq(filters.length,5,'five evidence filters');for(let mask=0;mask<32;mask++){filters.forEach((f,i)=>change(f,!!(mask&(1<<i))));const expected=filters.reduce((n,f)=>n+(f.checked?(f.dataset.evidence==='runtime'?2:1):0),0);eq(qa('.evidence-card').length,expected,'evidence combination '+mask);check($('evidence-count').textContent.startsWith(expected+' of 6'),'evidence count '+mask)}categories.push('all 32 evidence-filter combinations including empty state');
// All failure scenarios and their native disclosure controls.
for(let i=0;i<5;i++){click('[data-scenario="'+i+'"]');eq(qa('#scenario-result details').length,2,'two reveals');for(const e of qa('#scenario-result details')){e.open=true;check(e.textContent.length>100,'scenario answer substance');e.open=false}eq(qa('[data-scenario][aria-pressed="true"]').length,1,'one failure active')}
categories.push('five failure scenarios and ten reveal panels');
// Quality formulas and undefined precision.
click('#metrics-reset');check($('metric-results').textContent.includes('96.8%'),'default precision');check($('metric-results').textContent.includes('60.0%'),'default recall');click('#metrics-guess');check($('metric-results').textContent.includes('78.3%'),'guess precision');check($('metric-results').textContent.includes('90.0%'),'guess recall');input('tp',0);input('fp',0);check($('metric-results').textContent.includes('N/A'),'zero predictions undefined');input('tp',100);input('fp',0);input('captured',0);check($('metric-results').textContent.includes('100.0%'),'perfect edges');check($('metric-results').textContent.includes('0.0%'),'independent zero capture');categories.push('precision/recall/capture arithmetic, presets and zero-prediction boundary');
// Capacity formulas/defaults and stability boundaries.
click('#capacity-reset');const expected=['1.16','115.74','34.72','115.74','32,000,000','15.00 GB','23.15','900.0 GB','33,444','9.5 min'];qa('#capacity-results .num').forEach((n,i)=>eq(n.textContent,expected[i],'default capacity value '+i));change($('include-backfill'),true);check(results()[6].includes('30.09'),'burst+backfill cores');check(results()[3].includes('150.46'),'burst+backfill rate');input('service',1);check(results()[9].includes('Does not drain'),'unstable queue');input('service',1000);check(results()[8].includes('0'),'no backlog at high capacity');check(results()[9].includes('0.0 min'),'zero drain time');input('daily','');check(!/NaN|undefined/.test($('capacity-results').textContent),'empty input avoids NaN');input('daily',-50);check(!/NaN|undefined/.test($('capacity-results').textContent),'negative clamped avoids NaN');input('daily',10000000);input('fields',1000);input('deps',100);check(!/NaN|undefined/.test($('capacity-results').textContent),'upper bounds finite');click('#capacity-reset');input('burst',1);input('service',100000/86400);check(results()[9].includes('0.0 min'),'equal arrival/capacity with no initial queue');categories.push('capacity equations, backfill toggle, empty/negative/max inputs and stable/unstable queues');
// OTel-first instrumentation hierarchy and every synthetic ATDD scenario combination.
for(const mode of ['auto','custom','sdk','hybrid']){click('[data-instrument="'+mode+'"]');eq(q('[data-instrument="'+mode+'"]').getAttribute('aria-pressed'),'true','instrument choice '+mode);check($('instrument-detail').textContent.length>150,'instrument detail');for(let mask=0;mask<32;mask++){qa('[data-atdd]').forEach((c,i)=>change(c,!!(mask&(1<<i))));eq(qa('#atdd-results .result-box').length,6,'six separate ATDD metrics');if(mask===0)check($('atdd-results').textContent.includes('N/A'),'empty ATDD denominator');else check(!/NaN|undefined/.test($('atdd-results').textContent),'ATDD finite counts')}}
click('[data-instrument="hybrid"]');qa('[data-atdd]').forEach(c=>change(c,true));check($('atdd-results').textContent.includes('5 / 5'),'all synthetic scenarios');check($('atdd-results').textContent.includes('6 / 8'),'hybrid remains incomplete');check($('atdd-results').textContent.includes('92 / 97'),'capture loss remains visible');check($('atdd-explanation').textContent.includes('synthetic'),'synthetic disclaimer');categories.push('four instrumentation modes × 32 ATDD scenario combinations, six denominators and loss/empty states');

// Four-hop overlay: independent expected fixtures, typed relationships and controls.
const select=(id,value)=>{$(id).value=value;$(id).dispatchEvent(new w.Event('change',{bubbles:true}))};
const hwRows=()=>qa('#hw-edge-rows tr');const hwPaths=()=>qa('#hw-graph svg > path');
const cumulative=[5,9,13,17],localCounts=[5,4,4,4],moneyFields=['netAmount','netAmount','net_amount','revenue_usd'];
const expectedOutputs=[
 {orderId:42,netAmount:'90.00',status:'PAID',occurredAt:'2026-10-06T10:00:00Z'},
 {orderId:42,netAmount:'90.00',status:'PAID',occurredAt:'2026-10-06T10:00:00Z'},
 {order_id:42,net_amount:'90.00',status:'PAID',occurred_at:'2026-10-06T10:00:00Z'},
 {revenue_date:'2026-10-06',revenue_usd:'90.00'}
];
const equalJson=(actual,expected,message)=>{assert.deepEqual(JSON.parse(actual),expected,message);assertions++};
for(let cycle=0;cycle<2;cycle++)for(let hop=0;hop<4;hop++){
 click('[data-hw-hop="'+hop+'"]');
 eq($('hw-step-label').textContent,'Hop '+(hop+1)+' of 4 · '+(hop+2)+' datasets reached','hop and dataset counts');
 eq(qa('[data-hw-hop][aria-pressed="true"]').length,1,'one selected walkthrough hop');
 eq($('hw-prev').disabled,hop===0,'walkthrough previous boundary');
 eq($('hw-next').disabled,hop===3,'walkthrough next boundary');
 for(const mode of ['boundaries','declaration','tested','conflict','unbound','missing']){
  select('hw-mode',mode);
  const expected={...expectedOutputs[hop]};if(mode==='conflict')expected[moneyFields[hop]]='100.00';
  equalJson($('hw-output').textContent,expected,'synthetic output '+hop+' '+mode);
  check($('hw-simulation').textContent.startsWith('SIMULATED'),'simulated evidence disclaimer');
  for(const view of ['dataset','field']){
   click('[data-hw-view="'+view+'"]');
   for(const indirect of [false,true]){
    change($('hw-indirect'),indirect);
    eq(hwRows().length,localCounts[hop]-(hop===3&&!indirect?2:0),'local typed-edge count');
    const statuses=qa('#hw-edge-rows .hw-status').map(e=>e.textContent);
    if(mode==='boundaries')check(statuses.every(t=>t==='SIMULATED STATIC_ONLY'),'boundaries cannot prove mappings');
    if(mode==='declaration')check(statuses.every(t=>t==='SIMULATED '+(hop===3?'ENGINE_DERIVED':'DECLARED_AND_EXECUTED')),'evidence mechanism kept distinct');
    if(mode==='tested')check(statuses.every(t=>t==='SIMULATED BOUNDED_TEST_SUPPORT'),'semantic support bounded');
    if(mode==='unbound')check(statuses.every(t=>t==='SIMULATED UNKNOWN_CONTEXT'),'context mismatch does not corroborate');
    if(mode==='missing')check(statuses.every(t=>t==='SIMULATED NOT_OBSERVED / UNKNOWN'),'missing telemetry leaves unknown');
    if(mode==='conflict'){
     eq(statuses.filter(t=>t==='SIMULATED '+(hop===0?'CONFLICT':'UPSTREAM_CONFLICT')).length,hop===0?2:1,'money-path conflict scope');
     eq(statuses.filter(t=>t==='SIMULATED BOUNDED_TEST_SUPPORT').length,hwRows().length-(hop===0?2:1),'unaffected fields preserved');
    }
    if(view==='dataset')eq(qa('#hw-graph svg > rect').length,hop+2,'cumulative dataset nodes');
    else {
     eq(hwPaths().length,cumulative[hop]-(hop===3&&!indirect?2:0),'cumulative field edges');
     eq(hwPaths().filter(e=>e.hasAttribute('stroke-dasharray')).length,hop===3&&indirect?2:0,'indirect roles separate');
    }
   }
  }
 }
}
click('#hw-reset');eq($('hw-prev').disabled,true,'overlay reset first hop');click('#hw-prev');eq($('hw-step-label').textContent,'Hop 1 of 4 · 2 datasets reached','disabled previous stable');
for(let n=0;n<3;n++)click('#hw-next');click('#hw-next');eq($('hw-step-label').textContent,'Hop 4 of 4 · 5 datasets reached','overlay next boundary stable');
click('[data-hw-view="field"]');select('hw-focus','revenue_usd');
const highlighted=hwPaths().filter(p=>p.getAttribute('opacity')==='1'&&!p.hasAttribute('stroke-dasharray')).map(p=>p.textContent);
eq(highlighted.length,5,'revenue trace reaches both source operands');
check(highlighted.some(x=>x.includes('amount_cents'))&&highlighted.some(x=>x.includes('discount_cents')),'both subtraction operands highlighted');
check(!highlighted.some(x=>x.includes('order_id')||x.includes('status')||x.includes('occurred')),'ID/status/time not direct revenue sources');
select('hw-focus','revenue_date');eq(hwPaths().filter(p=>p.getAttribute('opacity')==='1'&&!p.hasAttribute('stroke-dasharray')).length,4,'timestamp direct date path');
check(hwPaths().filter(p=>p.hasAttribute('stroke-dasharray')).some(p=>p.textContent.includes('FILTER')),'filter is indirect');
check(hwPaths().filter(p=>p.hasAttribute('stroke-dasharray')).some(p=>p.textContent.includes('GROUP_BY')),'grouping is indirect');
for(let hop=0;hop<4;hop++){
 click('[data-hw-hop="'+hop+'"]');click('[data-hw-view="field"]');
 for(const option of [...$('hw-focus').options]){
  select('hw-focus',option.value);check($('hw-graph-note').textContent.includes(option.value==='all'?'All direct paths shown':option.value),'output focus trace '+hop+' '+option.value);
 }
}
click('[data-hw-agg="SUM"]');check($('hw-agg-result').textContent.includes('$140.00'),'multirow SUM result');
click('[data-hw-agg="MAX"]');check($('hw-agg-result').textContent.includes('$90.00'),'multirow MAX mutation detected');
check($('hw-agg-result').textContent.includes('One paid row alone: both return 90.00'),'one-row limitation explicit');
click('#hw-reset');eq($('hw-mode').value,'boundaries','overlay reset evidence');eq($('hw-focus').value,'all','overlay reset focus');eq($('hw-indirect').checked,true,'overlay reset controls');eq(q('[data-hw-view="dataset"]').getAttribute('aria-pressed'),'true','overlay reset view');check($('hw-agg-result').textContent.includes('$140.00'),'overlay reset aggregate');
check(q('#journey h2').textContent.includes('FX'),'legacy FX example explicitly distinct');
check($('walkthrough').textContent.includes('five datasets')&&$('walkthrough').textContent.includes('logical contract dataset'),'opt-in dataset model explicit');
check($('walkthrough').textContent.includes('USD only')&&$('walkthrough').textContent.includes('UTC'),'units and timezone explicit');
eq(errors.length,0,'overlay interactions error-free');
categories.push('four-hop overlay across 192 hop/evidence/view/control states, exact fixtures, 17 typed edges, direct ancestors, mutations and reset');

// Keyboard focus is retained when a hop-button rerender replaces its DOM node.
for(let hop=0;hop<4;hop++){const b=q('[data-hw-hop=\"'+hop+'\"]');b.focus();b.click();eq(d.activeElement.getAttribute('data-hw-hop'),String(hop),'activated hop retains focus');}
// Companion Markdown uses the same independently specified input/output fixture.
const markdownFile=path.join(path.dirname(file),'static-runtime-reconciliation-deep-dive.md');const markdown=fs.readFileSync(markdownFile,'utf8');
const markdownJson=[...markdown.matchAll(/```json\n([\s\S]*?)```/g)].map(m=>JSON.parse(m[1]));
eq(markdownJson.length,12,'all twelve Markdown JSON examples parse');
const expectedSource={order_id:42,amount_cents:10000,discount_cents:1000,status:'PAID',created_at:'2026-10-06T10:00:00Z'};
equalJson(JSON.stringify(markdownJson[0]),expectedSource,'Markdown initial source fixture');
for(let hop=0;hop<4;hop++){equalJson(JSON.stringify(markdownJson[1+2*hop]),hop===0?expectedSource:expectedOutputs[hop-1],'Markdown hop input '+hop);equalJson(JSON.stringify(markdownJson[2+2*hop]),expectedOutputs[hop],'Markdown hop output '+hop);}
check(!/\/workspace\/|\/root\/|dream_notes|libfile_/i.test(markdown),'Markdown contains no private paths or internal artifact IDs');
check(markdown.includes('SUM = 140.00, MAX = 90.00')&&markdown.includes('UNPAID 30.00'),'Markdown discriminating fixture');
check(markdown.includes('four hops and five datasets')&&markdown.includes('explicitly opted-in'),'Markdown dataset boundary explicit');
check(markdown.includes('separate from the executed Petclinic experiment'),'Markdown proposed/executed scope distinction');
categories.push('keyboard focus retention and companion Markdown JSON/fixture/privacy consistency');

// Practice controls and global reset.
eq(qa('.challenge').length,12,'twelve challenge cards');click('#expand-answers');eq(qa('.challenge[open]').length,12,'all revealed');click('#close-answers');eq(qa('.challenge[open]').length,0,'all hidden');qa('.practice-check').forEach(x=>change(x,true));eq($('practice-progress').textContent,'6 of 6 rehearsal checkpoints complete.','practice progress');click('#reset-all');eq(q('[data-arch="local"]').getAttribute('aria-pressed'),'true','reset architecture');eq($('step-label').textContent,'Step 1 of 7','reset trace');eq(qa('.evidence-card').length,6,'reset evidence');eq(qa('.practice-check:checked').length,0,'reset checklist');eq($('daily').value,'100000','reset capacity');eq($('tp').value,'60','reset quality');eq(qa('[data-atdd]:checked').length,1,'reset ATDD scenarios');eq(q('[data-instrument="hybrid"]').getAttribute('aria-pressed'),'true','reset instrumentation');eq($('hw-mode').value,'boundaries','global reset overlay evidence');eq($('hw-step-label').textContent,'Hop 1 of 4 · 2 datasets reached','global reset overlay hop');eq(errors.length,0,'no JS/CSS errors after interactions');categories.push('12 technical answers, practice checklist and global reset');
// Privacy and local source link checks; no URL network fetches.
check(!/\/workspace\/|\/root\/|private user/i.test(html),'no private paths/context');const pinned=qa('a[href*="github.com/Kart-rc/lineage-collection/blob/"]');for(const a of pinned){check(a.href.includes('/32958a35cdf49341b15a4d6cb05f596811396b06/'),'pinned code link');const rel=a.href.split('/32958a35cdf49341b15a4d6cb05f596811396b06/')[1].split('#')[0];check(fs.existsSync(path.join(process.cwd(),rel)),'pinned source exists locally '+rel)}categories.push('public-content scan and pinned source path checks');
const report={passed:true,assertions,categories,jsErrors:errors,sourceFile:path.basename(file),sourceBytes:Buffer.byteLength(html),sourceSha256:require('node:crypto').createHash('sha256').update(html).digest('hex'),markdownFile:path.basename(markdownFile),markdownBytes:Buffer.byteLength(markdown),markdownSha256:require('node:crypto').createHash('sha256').update(markdown).digest('hex'),externalSourceLinks:qa('a[href^="https:"]').length,limitations:['JSDOM does not lay out pixels; no screenshot, clipping, responsive geometry or real-browser claim.','External source links were inherited from primary-source research and checked structurally, not all re-fetched by this QA script.','Native accessibility/browser/keyboard behavior is not fully established by DOM checks.']};console.log(JSON.stringify(report,null,2));dom.window.close();
