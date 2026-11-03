const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../app.js'),'utf8');
const start = source.indexOf('function renderIntersection(');
const end = source.indexOf('function renderTiming(', start);
const element = {innerHTML:'', className:'', get textContent(){ return this.innerHTML.replace(/<[^>]*>/g,''); }};
const context = vm.createContext({$:()=>element});
vm.runInContext(source.slice(source.indexOf('function escapeHtml('),source.indexOf('function applyUiSettings(')),context);
vm.runInContext(source.slice(start,end),context);
const now = Date.parse('2026-11-03T15:00:00Z');
const iso = new Date(now).toISOString();
function fixture(fg=9,vix=28,rsi=30){return {meta:{updated_at_utc_iso:iso},timing:{fear_greed:{score:fg,available:true,timestamp:iso},metrics:{vix,spy_rsi14:rsi},source_as_of:{vix:iso,spy_rsi14:iso}}};}
for(let mask=0;mask<8;mask++){
  const data=fixture(mask&1?9:10,mask&2?28:27,mask&4?30:31);
  context.renderIntersection(data,now);
  const count=Number(Boolean(mask&1))+Number(Boolean(mask&2))+Number(Boolean(mask&4));
  assert.match(element.textContent,new RegExp(count+'/3'));
  if(count===2)assert.match(element.textContent,/웹 표시만/);
  if(count===3)assert.match(element.textContent,/Telegram 알림 대상/);
}
for(const value of [null,undefined,'',true,'9',NaN,Infinity]){
  const data=fixture();data.timing.fear_greed.score=value;
  context.renderIntersection(data,now);
  assert.match(element.textContent,/판정 보류/);
}
for(const key of ['vix','spy_rsi14']){
  const data=fixture();data.timing.source_as_of[key]='2026-10-01T00:00:00Z';
  context.renderIntersection(data,now);assert.match(element.textContent,/판정 보류/);
}
context.renderIntersection(fixture(),now+2*3600000+1);
assert.match(element.textContent,/판정 보류/);
new vm.Script(source); // Syntax-check the complete production app.
console.log('Web intersection tests passed: 8 combinations, missing values, stale data, full app syntax');
