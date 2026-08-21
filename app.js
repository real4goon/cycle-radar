const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const AUTO_REFRESH_MS = 5 * 60 * 1000;
let lastData = null;
let nextBrowserRefreshAt = Date.now() + AUTO_REFRESH_MS;
let uiFontScale = Number(localStorage.getItem('radarFontScale') || '1');
let uiTheme = localStorage.getItem('radarThemeV16') || 'night';
let currentFilter = 'all';
let selectedSector = null;

function escapeHtml(v=''){return String(v).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
function applyUiSettings(){
  document.documentElement.dataset.theme=uiTheme;
  document.documentElement.style.setProperty('--ui-zoom',String(uiFontScale));
  const t=$('#themeToggle'); if(t)t.textContent=uiTheme==='day'?'🌙 나이트':'☀ 데이';
}
function setFontScale(v){uiFontScale=Math.max(.9,Math.min(1.1,Number(v.toFixed(1))));localStorage.setItem('radarFontScale',String(uiFontScale));applyUiSettings();}
function clsForStatus(s=''){if(s.includes('상승')||s.includes('강함')||s.includes('개선'))return 'state-good';if(s.includes('약화')||s.includes('부담')||s.includes('위험'))return 'state-bad';if(s.includes('초기')||s.includes('주의')||s.includes('냉각'))return 'state-warn';return 'state-mid';}
function chipClass(s=''){return s.includes('상승')?'upcycle':s.includes('초기')?'early':s.includes('약화')?'weak':'neutral';}
function actionClass(action=''){if(action==='분할 접근')return 'good signal-buy';if(action==='소액 진입 검토')return 'entry signal-entry';if(action.includes('보류'))return 'bad';if(action.includes('추격주의'))return 'warn chase';if(action.includes('대기')||action.includes('주의'))return 'warn';return 'watch';}
function scoreFillClass(status=''){return status.includes('상승')?'fill-good':status.includes('초기')?'fill-early':status.includes('약화')?'fill-weak':'fill-neutral';}
function parseTimestamp(meta={},d={}){const raw=meta.updated_at_iso||meta.updated_at_kst||d.updated_at_kst;if(!raw)return null;let text=String(raw).trim();if(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(text))text=text.replace(' ','T')+':00+09:00';const t=Date.parse(text);return Number.isFinite(t)?t:null;}
function minutesSince(meta={},d={}){const t=parseTimestamp(meta,d);return t===null?null:Math.max(0,Math.floor((Date.now()-t)/60000));}
function ageText(mins){if(mins===null)return '경과시간 확인 불가';if(mins<1)return '방금 수집';if(mins<60)return `${mins}분 전 수집`;return `${Math.floor(mins/60)}시간 ${mins%60}분 전 수집`;}
function updateFreshness(meta={},d={}){const age=minutesSince(meta,d),dot=$('#freshDot');dot.className='status-dot';let label='수집 시점 확인';if(age!==null&&age<=35){label='최근 데이터';dot.classList.add('fresh')}else if(age!==null&&age<=70){label='갱신 대기';dot.classList.add('warn')}else if(age!==null){label='오래된 데이터 주의';dot.classList.add('stale')}$('#freshLabel').textContent=label;$('#dataAge').textContent=ageText(age);}
function openSourceModal(title,subtitle,sources=[]){$('#sourceModalTitle').textContent=title;$('#sourceModalSubtitle').textContent=subtitle||'';$('#sourceButtons').innerHTML=(sources||[]).map(s=>`<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener"><b>${escapeHtml(s.label)}</b><small>${escapeHtml(s.note||'')}</small></a>`).join('')||'<div class="source-note">연결된 외부 출처가 없습니다.</div>';$('#sourceModal').classList.add('open');$('#sourceModal').setAttribute('aria-hidden','false');}
function closeSourceModal(){$('#sourceModal').classList.remove('open');$('#sourceModal').setAttribute('aria-hidden','true');}
function closeSectorModal(){$('#sectorModal').classList.remove('open');$('#sectorModal').setAttribute('aria-hidden','true');}
function sparkSvg(values=[],positive=true){if(!Array.isArray(values)||values.length<2)return '';const w=120,h=24,min=Math.min(...values),max=Math.max(...values),range=(max-min)||1;const pts=values.map((v,i)=>`${(i/(values.length-1))*w},${h-((v-min)/range)*(h-3)-1}`).join(' ');return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}" stroke="${positive?'var(--green)':'var(--red)'}"/></svg>`;}
function extractReason(reason=''){
  const text=String(reason).replace('프리/정규 최신가 반영 · ','');
  const parts=text.split(' · ');
  const preferred=parts.filter(x=>/20일|SPY 대비|거래량|5일/.test(x));
  return preferred.slice(0,3);
}
function renderMarket(d){
  $('#marketStrip').innerHTML=(d.market||[]).map((m,i)=>`<div class="market-item"><div class="market-top"><div class="market-name">${escapeHtml(m.name)}</div><button class="source-icon" data-market-source="${i}" title="출처·시세">ⓘ</button></div><div class="market-value-row"><div class="market-value">${escapeHtml(m.value)}</div><div class="market-change ${(m.change_direction??m.change_pct)>=0?'positive':'negative'}">${escapeHtml(m.change_text||((m.change_pct>=0?'+':'')+Number(m.change_pct||0).toFixed(2)+'%'))}</div></div>${sparkSvg(m.spark||[],(m.change_direction??m.change_pct)>=0)}</div>`).join('');
  $$('[data-market-source]').forEach(btn=>btn.onclick=()=>{const m=d.market[Number(btn.dataset.marketSource)];openSourceModal(m.name,`${m.value} · ${m.change_text||''}`,m.sources||[])});
}
function renderEnvironment(d){const env=d.regime?.environment||{};$('#environmentScore').textContent=`${env.score??'-'}/100점`;$('#environmentGrade').textContent=(env.label||'-').replace(/^[^\s]+\s*/,'');$('#environmentGuidance').textContent=env.guidance||'시장 환경 해석 중';$('#environmentReasons').innerHTML=(d.regime?.reasons||[]).map(x=>`<div>• ${escapeHtml(x)}</div>`).join('');$$('#environmentScale span').forEach(x=>x.classList.remove('active'));const s=Number(env.score);const range=s>=70?'favorable':s>=55?'selective':s>=40?'caution':'risk';$(`#environmentScale [data-range="${range}"]`)?.classList.add('active');}
function timingTone(kind,score){
  const n=Number(score);
  if(!Number.isFinite(n))return 'tone-mid';
  if(kind==='fear'){
    if(n<45)return 'tone-danger';
    if(n<55)return 'tone-mid';
    if(n<75)return 'tone-warn';
    return 'tone-danger';
  }
  if(n>=75)return 'tone-good';
  if(n>=60)return 'tone-cyan';
  if(n>=45)return 'tone-mid';
  return 'tone-warn';
}
function timingCard(title,obj,kind){
  const score=(obj&&obj.score!==null&&obj.score!==undefined)?Math.round(Number(obj.score)):null;
  const label=obj?.label||obj?.rating_ko||obj?.rating||'확인 중';
  const note=obj?.note||obj?.guidance||'';
  const tone=timingTone(kind,score);
  const width=score===null?0:Math.max(0,Math.min(100,score));
  return `<div class="timing-card ${tone}"><div class="timing-card-top"><span>${escapeHtml(title)}</span><span class="timing-label">${escapeHtml(label)}</span></div><div class="timing-score-row"><b>${score===null?'-':score}</b><span>/100</span></div><div class="timing-meter"><span style="width:${width}%"></span></div><small>${escapeHtml(note)}</small></div>`;
}
function renderTiming(d){
  const t=d.timing||{};
  const low=t.low_buy||{};
  const rev=t.reversal||{};
  const fg=t.fear_greed||{};
  $('#timingGrid').innerHTML=timingCard('저점매수 매력도',low,'low')+timingCard('반전 확인도',rev,'reversal')+timingCard('Fear & Greed',fg,'fear');
  const summary=t.summary||'저점 매력과 실제 반전 신호를 함께 확인합니다.';
  const action=t.action?`<b>${escapeHtml(t.action)}</b>`:'';
  $('#timingSummary').innerHTML=`<span>${escapeHtml(summary)}</span>${action}`;
}
function sectorRows(d){return (d.sectors||[]).filter(s=>currentFilter==='all'||s.status===currentFilter);}
function renderRadar(d){
  const rows=sectorRows(d);
  $('#sectorTable').innerHTML=rows.map((s,i)=>{const reasons=extractReason(s.reason);const idx=(d.sectors||[]).indexOf(s);return `<tr data-sector-index="${idx}"><td><span class="sector-name">${escapeHtml(s.name)}</span><span class="etf-sub">(${escapeHtml((s.etfs||[]).join(' / '))})</span></td><td><span class="chip ${chipClass(s.status)}">${escapeHtml(s.status)}</span></td><td><div class="score-wrap"><span class="score-num">${s.score}</span><span class="score-bar"><span class="score-fill ${scoreFillClass(s.status)}" style="width:${Math.max(0,Math.min(100,s.score))}%"></span></span></div></td><td><span class="action ${actionClass(s.action)}">${escapeHtml(s.action)}</span></td><td class="reason-mini">${reasons.map(x=>`<span>• ${escapeHtml(x)}</span>`).join('')}</td><td class="row-arrow">›</td></tr>`}).join('');
  $('#sectorCards').innerHTML=rows.map(s=>{const idx=(d.sectors||[]).indexOf(s);return `<div class="sector-card" data-sector-index="${idx}"><div class="sector-card-top"><div><b>${escapeHtml(s.name)}</b><small>${escapeHtml((s.etfs||[]).join(' / '))}</small></div><span class="chip ${chipClass(s.status)}">${escapeHtml(s.status)}</span></div><div class="score-wrap mobile-score"><span class="score-num">${s.score}</span><span class="score-bar"><span class="score-fill ${scoreFillClass(s.status)}" style="width:${s.score}%"></span></span></div><div class="action ${actionClass(s.action)}">${escapeHtml(s.action)}</div><div class="reason-mini">${extractReason(s.reason).map(x=>`<span>• ${escapeHtml(x)}</span>`).join('')}</div></div>`}).join('');
  $$('[data-sector-index]').forEach(el=>el.onclick=()=>openSectorDetail(d.sectors[Number(el.dataset.sectorIndex)]));
}
function openSectorDetail(s){selectedSector=s;$('#sectorModalTitle').textContent=`${s.name} · ${(s.etfs||[]).join(' / ')}`;$('#sectorModalMeta').textContent=`레이더 ${s.score}점 · ${s.status}`;const f=s.factors||{};const factors=[['가격 모멘텀',f.momentum],['상대강도',f.relative_strength],['거래량/수급',f.volume],['Macro 환경',f.macro]];$('#factorBars').innerHTML=factors.map(([n,v])=>`<div class="factor"><div class="factor-name">${n}</div><div class="factor-bar"><div class="factor-fill" style="width:${Number(v||0)}%"></div></div><div class="factor-score">${v??'-'}/100</div></div>`).join('');$('#sectorDetailReason').textContent=s.reason||'';$('#sectorDetailAction').textContent=`현재 판단: ${s.action}`;$('#sectorQuoteBtn').onclick=()=>openSourceModal(`${s.etfs?.[0]||''} 시세·차트`,s.name,s.quote_sources||[]);$('#sectorModal').classList.add('open');$('#sectorModal').setAttribute('aria-hidden','false');}
function renderChanges(d){const c=d.changes||{};const improved=c.improved||[],worsened=c.worsened||[];const column=(title,arr,type)=>`<div class="change-column ${type}"><div class="change-title ${type}">${type==='improve'?'↗ 상태 개선':'↘ 상태 약화'}</div>${arr.length?arr.slice(0,3).map((x,i)=>`<div class="change-item"><span class="change-rank">${i+1}</span><div><div class="change-name">${escapeHtml(x.symbol||'')} <span class="muted">(${escapeHtml(x.name||'')})</span></div><div class="change-flow">${escapeHtml(x.from_status||'')} → <b>${escapeHtml(x.to_status||'')}</b></div></div><div class="change-delta ${x.delta>=0?'positive':'negative'}">${x.delta>=0?'+':''}${x.delta}점</div></div>`).join(''):'<div class="unchanged-summary">뚜렷한 변화 없음</div>'}</div>`;$('#changesGrid').innerHTML=column('개선',improved,'improve')+column('악화',worsened,'worsen');$('#unchangedSummary').textContent=c.note||`변화 없음: ${(c.unchanged||[]).join(', ')||'-'}`;}
function renderEvents(d){$('#events').innerHTML=(d.events||[]).map((e,i)=>`<div class="event"><div class="event-name">${escapeHtml(e.name)}</div><div class="event-date">${escapeHtml(e.date_kst||e.when||'')}</div><div class="event-time">${escapeHtml(e.time_kst||'')} KST</div><div class="event-impact">${'★'.repeat(Number(e.impact||1))}${'☆'.repeat(Math.max(0,3-Number(e.impact||1)))}</div>${e.source_url?`<button class="event-source-btn" data-event-source="${i}">공식 일정 보기</button>`:''}</div>`).join('');$$('[data-event-source]').forEach(b=>b.onclick=()=>{const e=d.events[Number(b.dataset.eventSource)];openSourceModal(e.name,`${e.date_et||''} ${e.time_et||''} ET`,[{label:e.source||'공식 일정',note:e.note||'',url:e.source_url}])});}
function renderMacro(d){$('#macroCards').innerHTML=(d.macro||[]).map((m,i)=>`<div class="macro-card panel"><div class="macro-top"><div class="macro-title">${escapeHtml(m.name)}</div><span class="macro-state ${clsForStatus(m.state)}">${escapeHtml(m.state)}</span></div><div class="macro-sub">${escapeHtml(m.summary)}</div><div class="macro-invest"><span class="macro-score">시장 영향 ${m.investment_score??'-'}/100</span><span class="chip ${m.investment_grade==='우호'?'upcycle':m.investment_grade==='주의'||m.investment_grade==='위험'?'weak':'neutral'}">${escapeHtml(m.investment_grade||'중립')}</span></div><div class="macro-hint"><b>투자 해석</b> · ${escapeHtml(m.investment_hint||'')}</div><button class="macro-source-btn" data-macro-source="${i}">근거 데이터 보기</button></div>`).join('');$$('[data-macro-source]').forEach(btn=>btn.onclick=()=>{const m=d.macro[Number(btn.dataset.macroSource)];openSourceModal(`${m.name} 근거 데이터`,m.summary,m.sources||[])});}
function renderCautions(d){$('#cautionList').innerHTML=(d.meta?.cautions||[]).map(x=>`<li>${escapeHtml(x)}</li>`).join('');}
function renderMeta(d){const m=d.meta||{};$('#updatedAt').textContent=`수집 ${m.updated_at_kst||d.updated_at_kst||'-'} KST · ${m.updated_at_et||'-'} ET`;$('#marketViewpoint').textContent=m.perspective||'데이터 수집 시점 기준';updateFreshness(m,d);}
function renderAll(d){lastData=d;renderMeta(d);renderMarket(d);renderEnvironment(d);renderTiming(d);renderRadar(d);renderChanges(d);renderEvents(d);renderMacro(d);renderCautions(d);}
async function loadData(manual=false){const btn=$('#manualRefresh');if(manual){btn.disabled=true;btn.textContent='확인 중…'}try{const r=await fetch(`data/dashboard.json?t=${Date.now()}`,{cache:'no-store'});if(!r.ok)throw new Error(`HTTP ${r.status}`);const d=await r.json();renderAll(d);nextBrowserRefreshAt=Date.now()+AUTO_REFRESH_MS;}catch(e){console.error(e);$('#freshLabel').textContent='데이터 로딩 오류';$('#freshDot').className='status-dot stale';}finally{if(manual){btn.disabled=false;btn.textContent='새로고침 ↻'}}}
function updateCountdown(){const sec=Math.max(0,Math.ceil((nextBrowserRefreshAt-Date.now())/1000));$('#refreshCountdown').textContent=`화면 자동 확인 ${Math.floor(sec/60)}:${String(sec%60).padStart(2,'0')} 후`;if(sec<=0)loadData();}

$('#themeToggle').onclick=()=>{uiTheme=uiTheme==='night'?'day':'night';localStorage.setItem('radarThemeV16',uiTheme);applyUiSettings();};
$('#fontSmaller').onclick=()=>setFontScale(uiFontScale-.1);$('#fontReset').onclick=()=>setFontScale(1);$('#fontLarger').onclick=()=>setFontScale(uiFontScale+.1);$('#manualRefresh').onclick=()=>loadData(true);
$('#sectorFilters').onclick=e=>{const b=e.target.closest('[data-filter]');if(!b)return;currentFilter=b.dataset.filter;$$('.filter-btn').forEach(x=>x.classList.remove('active'));b.classList.add('active');if(lastData)renderRadar(lastData);};
$('#environmentDetailBtn').onclick=()=>{if(!lastData)return;const src=(lastData.macro||[]).flatMap(m=>(m.sources||[]).slice(0,1));openSourceModal('오늘의 투자 환경 근거','고용·물가·금리·경기·유동성·위험선호를 종합한 규칙 기반 점수',src);};
$('#timingDetailBtn').onclick=()=>{if(!lastData)return;const t=lastData.timing||{};const src=t.sources||[];const formula='저점매수 매력도 = Fear & Greed 역산 30% + S&P500 고점 대비 낙폭 25% + RSI 15% + VIX 15% + 시장 폭 스트레스 15% · 반전 확인도 = 5일 모멘텀 25% + 20일선 회복 25% + VIX 안정 20% + 시장 폭 개선 15% + 거래량 확인 15%';openSourceModal('시장 타이밍 신호 계산 기준',formula,src);};
$$('[data-close-modal]').forEach(x=>x.onclick=closeSourceModal);$$('[data-close-sector]').forEach(x=>x.onclick=closeSectorModal);
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeSourceModal();closeSectorModal();}});
applyUiSettings();loadData();setInterval(updateCountdown,1000);
