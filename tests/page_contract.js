// Integration check on the Python -> browser seam of the preview page.
//
// The pytest suite covers the pure Python logic; this covers the contract between what
// build_preview() EMITS and what the simulator JS CONSUMES -- a seam no unit test sees.
// Run it against a built page (see aidlc-docs/construction/build-and-test/):
//
//   python -m content_hub.cli social preview <CAL_ID> --no-publish
//   node tests/page_contract.js <page>.html <extracted-script>.js
//
// Requires Node. Exits non-zero on any failure.

const fs=require('fs');
const html=fs.readFileSync(process.argv[2],'utf8');
const js=fs.readFileSync(process.argv[3],'utf8');
let fail=0;
const ok=(n,c)=>{console.log((c?'PASS ':'FAIL ')+n); if(!c)fail++;};

// the browser evaluates these two literals; if they don't parse, the simulator is dead
const posts=eval(js.match(/var SIM_POSTS = (\[[\s\S]*?\]);\r?\nvar ICONS/)[1]);
const icons=eval('('+js.match(/var ICONS = (\{[\s\S]*?\});\r?\n/)[1]+')');
ok('SIM_POSTS parses as JS', Array.isArray(posts));
ok('ICONS parses as JS', typeof icons==='object');

// every icon the builders ask for must exist, or a control renders blank
const used=[...js.matchAll(/icon\('([a-z]+)'/g)].map(m=>m[1]);
const missing=[...new Set(used)].filter(n=>!icons[n]);
ok('every icon used by the builders exists ('+new Set(used).size+' distinct)', missing.length===0);
if(missing.length) console.log('   missing:',missing);

// the clone contract: every SIM_POSTS id must have a card to clone from
const ids=new Set([...html.matchAll(/<article class="card [^>]*data-rowid="([^"]*)"/g)].map(m=>m[1]));
const orphans=posts.filter(p=>!ids.has(p.id));
ok('every SIM_POSTS row has a matching card ('+posts.length+' rows)', orphans.length===0);
if(orphans.length) console.log('   orphans:',orphans.slice(0,5).map(p=>p.id));

// FR-4 ordering
const dated=posts.filter(p=>p.date).map(p=>p.date);
ok('dated rows are newest-first', dated.every((d,i)=>i===0||dated[i-1]>=d));
const firstUndated=posts.findIndex(p=>!p.date);
ok('undated rows sort last', firstUndated===-1||posts.slice(firstUndated).every(p=>!p.date));

// FR-10 coherence, on the real data rather than generated samples
const bad=posts.filter(p=>p.eng.comments>p.eng.likes||('views' in p.eng && p.eng.likes>p.eng.views));
ok('engagement coherent on all rows', bad.length===0);

// FR-6 membership
const byP={};posts.forEach(p=>byP[p.p]=(byP[p.p]||0)+1);
console.log('   platforms:',JSON.stringify(byP),'| reels:',posts.filter(p=>p.reel).length,
            '| carousels:',posts.filter(p=>p.car).length,'| video posts:',posts.filter(p=>p.kind==='video').length);
ok('only the three known platforms', Object.keys(byP).every(k=>['instagram','facebook','tiktok'].includes(k)));
process.exit(fail?1:0);
