const $ = (s) => document.querySelector(s);
const AUTO_REFRESH_MS = 5 * 60 * 1000;
let lastData = null;
let selectedSymbol = null;
let selectedSector = null;
let selectedWatch = null;
let nextBrowserRefreshAt = Date.now() + AUTO_REFRESH_MS;
let uiFontScale = Number(localStorage.getItem('radarFontScale') || '1');
let uiTheme = localStorage.getItem('radarThemeV15') || 'night';

function applyUiSettings(){
  document.documentElement.dataset.theme = uiTheme;
  document.documentElement.style.setProperty('--ui-zoom', String(uiFontScale));
  const t=$('#themeToggle');
  if(t){ t.textContent = uiTheme==='day' ? '🌙 나이트' : '☀ 데이'; t.title = uiTheme==='day' ? '나이트 화면으로 전환' : '데이 화면으로 전환'; }
}
function setFontScale(v){
  uiFontScale=Math.max(.9,Math.min(1.1,Number(v.toFixed(1))));
  localStorage.setItem('radarFontScale',String(uiFontScale));
  applyUiSettings();
}
function gradeScore(grade='중립'){
  const g=String(grade);
  if(g.includes('우호')) return 80;
  if(g.includes('위험')) return 15;
  if(g.includes('주의')) return 35;
  return 55;
}
function macroFallbackHint(name,grade='중립'){
  const g=String(grade);
  const map={
    '고용': g.includes('주의')?'고용 둔화가 빨라지는 구간으로 경기민감주에는 부담입니다.':'고용은 아직 시장을 크게 훼손하지 않는 범위입니다.',
    '물가': g.includes('우호')?'물가 둔화는 금리 안정에 도움을 줘 성장주·바이오·리츠에 우호적입니다.':'물가가 다시 강해지면 금리 상승 압력이 커져 성장주에 부담입니다.',
    '금리': g.includes('주의')?'장기금리 부담이 커 고밸류 성장주·반도체·바이오·리츠에 불리합니다.':'금리 부담이 크게 확대되지 않는 중립 구간입니다.',
    '경기': g.includes('주의')?'경기 둔화 신호가 강해지면 산업재·소비·금융 등 경기민감 자산에 부담입니다.':'경기 신호는 아직 중립 범위입니다.',
    '유동성': g.includes('우호')?'달러·유동성 환경이 위험자산에 비교적 우호적입니다.':g.includes('주의')?'달러 강세·유동성 부담은 위험자산에 불리합니다.':'유동성 환경은 뚜렷한 방향이 없는 중립입니다.',
    '위험선호': g.includes('우호')?'변동성이 낮아 위험자산 선호에 우호적입니다.':g.includes('위험')||g.includes('주의')?'변동성 확대 구간으로 고베타·레버리지 자산에 주의가 필요합니다.':'위험선호는 중립 범위입니다.'
  };
  return map[name]||'시장 영향은 현재 중립 범위로 해석합니다.';
}

const clsForStatus = (s='') => {
  if (s.includes('상승') || s.includes('강함') || s.includes('개선')) return 'state-good';
  if (s.includes('약화') || s.includes('부담') || s.includes('위험')) return 'state-bad';
  if (s.includes('초기') || s.includes('주의') || s.includes('냉각')) return 'state-warn';
  return 'state-mid';
};
const chipClass = (s='') => s.includes('상승') ? 'upcycle' : s.includes('초기') ? 'early' : s.includes('약화') ? 'weak' : 'neutral';
const investClass = (s='') => s.includes('우호') ? 'invest-good' : s.includes('위험') ? 'invest-risk' : s.includes('주의') ? 'invest-caution' : 'invest-neutral';

function escapeHtml(v='') { return String(v).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function parseTimestamp(meta={}, d={}) {
  const candidates=[meta.updated_at_iso,meta.updated_at_kst,d.updated_at_kst].filter(Boolean);
  for(const raw of candidates){let text=String(raw).trim();if(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(text))text=text.replace(' ','T')+':00+09:00';else if(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(text))text+=':00+09:00';const t=Date.parse(text);if(Number.isFinite(t))return t;}return null;
}
function minutesSince(meta={},d={}){const t=parseTimestamp(meta,d);return t===null?null:Math.max(0,Math.floor((Date.now()-t)/60000));}
function ageText(mins){if(mins===null)return '데이터 경과시간 확인 불가';if(mins<1)return '방금 수집';if(mins<60)return `${mins}분 전 수집`;const h=Math.floor(mins/60),m=mins%60;return `${h}시간 ${m}분 전 수집`;}
function updateFreshness(meta={},d={}){const age=minutesSince(meta,d),dot=$('#freshDot');dot.className='status-dot';let label='수집 시점 확인';if(age!==null&&age<=35){dot.classList.add('fresh');label='최근 데이터';}else if(age!==null&&age<=70){dot.classList.add('warn');label='갱신 대기';}else if(age!==null){dot.classList.add('stale');label='오래된 데이터 주의';}else{dot.classList.add('warn');label='시간 정보 확인 필요';}$('#freshLabel').textContent=label;$('#dataAge').textContent=ageText(age);}

function genericQuoteSources(symbol){
  const s=encodeURIComponent(symbol);
  const lower=String(symbol).toLowerCase();
  return [
    {label:'Yahoo Finance',note:'현재 무료 수집 데이터와 동일 계열',url:`https://finance.yahoo.com/quote/${s}/`},
    {label:'Nasdaq',note:'미국 상장 ETF/주식 조회',url:`https://www.nasdaq.com/market-activity/etf/${lower}`},
    {label:'Investing.com',note:'검색 결과에서 시세·차트 확인',url:`https://www.investing.com/search/?q=${s}`},
    {label:'Bloomberg',note:'검색 결과에서 시세·뉴스 확인',url:`https://www.bloomberg.com/search?query=${s}`}
  ];
}
function fallbackMarketSources(m){
  const map={
    'NQ=F':[{label:'CME Group',note:'E-mini Nasdaq-100 선물 공식 시장',url:'https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.html'}],
    'ES=F':[{label:'CME Group',note:'E-mini S&P 500 선물 공식 시장',url:'https://www.cmegroup.com/markets/equities/sp/e-mini-sandp500.html'}],
    '^SOX':[{label:'Nasdaq',note:'PHLX Semiconductor Sector Index',url:'https://www.nasdaq.com/market-activity/index/sox'}],
    'CL=F':[{label:'CME Group',note:'WTI Crude Oil 선물 공식 시장',url:'https://www.cmegroup.com/markets/energy/crude-oil/light-sweet-crude.html'}],
    '^TNX':[{label:'FRED',note:'미국 10년 국채금리 DGS10',url:'https://fred.stlouisfed.org/series/DGS10'}],
    'DX-Y.NYB':[{label:'ICE',note:'U.S. Dollar Index 선물 공식 시장',url:'https://www.ice.com/products/194/US-Dollar-Index-Futures'}],
    'BTC-USD':[{label:'Coinbase',note:'BTC-USD 시세',url:'https://www.coinbase.com/price/bitcoin'}],
    'KRW=X':[{label:'한국은행 ECOS',note:'공식 경제통계 시스템',url:'https://ecos.bok.or.kr/'}]
  };
  const arr=[...(map[m.symbol]||[])];
  if(m.source_url)arr.push({label:m.source_label||'Yahoo Finance',note:'대시보드 수집 시세 출처',url:m.source_url});
  arr.push({label:'Investing.com',note:'대체 시세·차트 조회',url:`https://www.investing.com/search/?q=${encodeURIComponent(m.name)}`});
  return arr;
}
function fallbackMacroSources(m){
  const map={
    '고용':[{label:'FRED · UNRATE',note:'미국 실업률 공식 시계열',url:'https://fred.stlouisfed.org/series/UNRATE'},{label:'BLS',note:'미 노동통계국 고용 통계',url:'https://www.bls.gov/cps/'}],
    '물가':[{label:'FRED · CPIAUCSL',note:'소비자물가지수 시계열',url:'https://fred.stlouisfed.org/series/CPIAUCSL'},{label:'BLS CPI',note:'미 노동통계국 CPI 원문',url:'https://www.bls.gov/cpi/'}],
    '금리':[{label:'FRED · DGS10',note:'미국 10년 국채금리',url:'https://fred.stlouisfed.org/series/DGS10'},{label:'U.S. Treasury',note:'미 재무부 금리 자료',url:'https://home.treasury.gov/resource-center/data-chart-center/interest-rates'}],
    '경기':[{label:'FRED · ICSA',note:'신규 실업수당 청구',url:'https://fred.stlouisfed.org/series/ICSA'},{label:'U.S. DOL',note:'미 노동부 실업보험 통계',url:'https://www.dol.gov/ui/data.pdf'}],
    '유동성':[{label:'Yahoo Finance · DXY',note:'달러인덱스 시세',url:'https://finance.yahoo.com/quote/DX-Y.NYB/'},{label:'ICE',note:'U.S. Dollar Index 공식 시장',url:'https://www.ice.com/products/194/US-Dollar-Index-Futures'}],
    '위험선호':[{label:'Cboe VIX',note:'VIX 공식 설명·시세',url:'https://www.cboe.com/tradable_products/vix/'},{label:'Yahoo Finance · VIX',note:'VIX 시세',url:'https://finance.yahoo.com/quote/%5EVIX/'}]
  };
  return m.sources?.length?m.sources:(map[m.name]||[]);
}
function openSourceModal(title,subtitle,sources=[]){
  const unique=[];const seen=new Set();for(const s of sources){if(!s?.url||seen.has(s.url))continue;seen.add(s.url);unique.push(s);}
  $('#sourceModalTitle').textContent=title;$('#sourceModalSubtitle').textContent=subtitle||'';
  $('#sourceButtons').innerHTML=unique.length?unique.map(s=>`<a class="source-button" href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer"><span>${escapeHtml(s.label)}</span><small>${escapeHtml(s.note||'새 탭 열기')} ↗</small></a>`).join(''):'<div class="source-subtitle">연결 가능한 외부 출처가 없습니다.</div>';
  $('#sourceModal').classList.add('open');$('#sourceModal').setAttribute('aria-hidden','false');
}
function closeSourceModal(){ $('#sourceModal').classList.remove('open');$('#sourceModal').setAttribute('aria-hidden','true'); }
document.addEventListener('click',e=>{if(e.target.closest('[data-close-modal]'))closeSourceModal();});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeSourceModal();});

async function load({manual=false}={}){
  const btn=$('#manualRefresh');if(manual){btn.disabled=true;btn.textContent='확인 중…';}
  try{
    const r=await fetch(`data/dashboard.json?t=${Date.now()}`,{cache:'no-store'});if(!r.ok)throw new Error(`HTTP ${r.status}`);const d=await r.json();lastData=d;const meta=d.meta||{};const kst=meta.updated_at_kst||d.updated_at_kst||'-';
    $('#updatedAt').textContent=`수집 ${kst} KST${meta.updated_at_et?` · ${meta.updated_at_et} ET`:''}`;$('#marketViewpoint').textContent=meta.perspective||'세션 정보 업데이트 대기';$('#updatePolicy').textContent=meta.update_policy||'서버 데이터는 예약 갱신됩니다.';$('#watchAsOf').textContent=`판정 기준 ${kst} KST${meta.perspective?` · ${meta.perspective}`:''}`;updateFreshness(meta,d);

    const env=d.regime?.environment||{};$('#environmentGrade').textContent=env.label||d.regime?.market_label||'중립';$('#environmentGrade').className=`environment-grade ${investClass(env.label||'중립')}`;$('#environmentScore').textContent=env.score!=null?`${env.score}/100점`:'규칙 기반';$('#environmentGuidance').textContent=env.guidance||'시장 환경 등급은 고용·물가·금리·경기·유동성·위험선호를 종합한 보조 신호입니다.';document.querySelectorAll('#environmentScale span').forEach(x=>x.classList.remove('active'));if(env.score!=null){const range=env.score>=70?'favorable':env.score>=55?'selective':env.score>=40?'caution':'risk';document.querySelector(`#environmentScale [data-range="${range}"]`)?.classList.add('active');}$('#regimeName').textContent=d.regime?.name||'-';$('#regimeReasons').innerHTML=(d.regime?.reasons||[]).map(x=>`• ${escapeHtml(x)}`).join('<br>');

    $('#marketStrip').innerHTML=(d.market||[]).map((m,i)=>{const changeText=m.change_text??`${m.change_pct>=0?'+':''}${Number(m.change_pct||0).toFixed(2)}%`;return `<div class="market-item"><div class="market-name">${escapeHtml(m.name)}</div><div class="market-value">${escapeHtml(m.value)}</div><div class="market-change ${(m.change_direction??m.change_pct)>=0?'positive':'negative'}">${escapeHtml(changeText)}</div><button class="source-trigger" type="button" data-market-source="${i}">출처·시세 보기</button></div>`;}).join('');
    document.querySelectorAll('[data-market-source]').forEach(el=>el.onclick=(e)=>{e.stopPropagation();const m=d.market[+el.dataset.marketSource];openSourceModal(`${m.name} · 출처/시세`,`${m.value} · ${m.change_text||''} · 수집 ${kst} KST`,m.sources?.length?m.sources:fallbackMarketSources(m));});

    $('#macroCards').innerHTML=(d.macro||[]).map((m,i)=>{const g=m.investment_grade||'중립';const sc=m.investment_score??gradeScore(g);const hint=m.investment_hint||macroFallbackHint(m.name,g);return `<div class="macro-card panel"><div class="macro-top"><div class="macro-title">${escapeHtml(m.name)}</div><div class="macro-state ${clsForStatus(m.state)}">${escapeHtml(m.state)}</div></div><div class="macro-sub">${escapeHtml(m.summary)}</div><div class="macro-invest"><span class="macro-invest-label">시장 영향 점수</span><span class="invest-badge ${investClass(g)}">${sc}/100 · ${escapeHtml(g)}</span></div><div class="macro-impact"><b>투자 해석</b> · ${escapeHtml(hint)}</div><button class="source-trigger macro-source-trigger" type="button" data-macro-source="${i}">근거 데이터 보기</button></div>`;}).join('');
    document.querySelectorAll('[data-macro-source]').forEach(el=>el.onclick=(e)=>{e.stopPropagation();const m=d.macro[+el.dataset.macroSource];openSourceModal(`${m.name} · 근거 데이터`,`${m.summary} · 시장 영향 ${m.investment_score??gradeScore(m.investment_grade||'중립')}/100 · ${m.investment_grade||'중립'}`,fallbackMacroSources(m));});

    $('#sectorTable').innerHTML=(d.sectors||[]).map(s=>`<tr><td class="sector-name">${escapeHtml(s.name)}</td><td><span class="chip ${chipClass(s.status)}">${escapeHtml(s.status)}</span></td><td class="score-cell">${s.score}</td><td><b>${s.etfs.map(escapeHtml).join(' / ')}</b></td><td class="action">${escapeHtml(s.action)}</td></tr>`).join('');
    $('#sectorCards').innerHTML=(d.sectors||[]).map(s=>`<div class="sector-card"><div class="sector-card-top"><div class="sector-name">${escapeHtml(s.name)}</div><span class="chip ${chipClass(s.status)}">${escapeHtml(s.status)}</span></div><div style="margin-top:8px"><b>${s.score}점</b> · ${s.etfs.map(escapeHtml).join(' / ')}</div><small>${escapeHtml(s.action)}</small></div>`).join('');

    const sectorBySymbol={};(d.sectors||[]).forEach(s=>s.etfs.forEach(e=>sectorBySymbol[e]=s));
    let top=(d.watchlist||[]).filter(w=>String(w.status||'')!=='중립');
    if(!top.length){
      const active=(d.sectors||[]).filter(s=>String(s.status||'')!=='중립');
      const opportunities=active.filter(s=>s.status==='상승 사이클'||s.status==='초기 관심').sort((a,b)=>b.score-a.score);
      const warnings=active.filter(s=>s.status==='약화').sort((a,b)=>a.score-b.score);
      const picked=[...opportunities.slice(0,3),...warnings.slice(0,1)];
      const used=new Set(picked.map(s=>s.name));
      for(const s of active){if(picked.length>=4)break;if(!used.has(s.name)){picked.push(s);used.add(s.name);}}
      top=picked.map(s=>({symbol:s.etfs[0],name:s.name,status:s.status,action:s.action,score:s.score,reason:s.reason,as_of_kst:kst}));
    }
    top=top.slice(0,4);
    const watchBySymbol=Object.fromEntries(top.map(w=>[w.symbol||w.etfs?.[0]||'',w]));
    $('#watchList').innerHTML=top.length?top.map((w,i)=>{const sym=w.symbol||w.etfs?.[0]||'';const sector=sectorBySymbol[sym]||{};const score=w.score??sector.score??'-';const reason=w.reason||sector.reason||'';const selected=(selectedSymbol?selectedSymbol===sym:i===0)?'selected':'';return `<div class="watch-card ${selected}" data-symbol="${escapeHtml(sym)}"><div class="watch-head"><div><div class="symbol">${escapeHtml(sym)}</div><div class="watch-meta">${escapeHtml(w.name||'')}</div></div><span class="chip ${chipClass(w.status)}">${escapeHtml(w.status)}</span></div><div class="watch-date">판정 기준 ${escapeHtml(w.as_of_kst||kst)} KST</div><div class="watch-action">${escapeHtml(w.action)}</div><div class="watch-score-row"><span class="watch-score">레이더 ${escapeHtml(score)}점</span><span class="watch-meta">중립 신호 제외</span></div>${reason?`<div class="watch-reason">${escapeHtml(reason)}</div>`:''}<button class="source-trigger watch-source-trigger" type="button" data-watch-source="${escapeHtml(sym)}">시세·차트 보기</button></div>`;}).join(''):'<div class="watch-empty">오늘은 중립을 제외하면 뚜렷한 관심 신호가 없습니다.<br>억지로 종목을 채우지 않고 비워두는 것이 정상입니다.</div>';
    const details={};(d.sectors||[]).forEach(s=>s.etfs.forEach(e=>details[e]=s));
    document.querySelectorAll('.watch-card').forEach(card=>{card.onclick=(e)=>{if(e.target.closest('[data-watch-source]'))return;selectedSymbol=card.dataset.symbol;selectedSector=details[selectedSymbol]||d.sectors[0];selectedWatch=watchBySymbol[selectedSymbol]||{symbol:selectedSymbol};document.querySelectorAll('.watch-card').forEach(x=>x.classList.remove('selected'));card.classList.add('selected');renderDetail(selectedSector,selectedWatch);};});
    document.querySelectorAll('[data-watch-source]').forEach(btn=>btn.onclick=(e)=>{e.stopPropagation();const sym=btn.dataset.watchSource;const w=watchBySymbol[sym]||{symbol:sym};openSourceModal(`${sym} · 시세/차트`,`오늘의 관심 ETF · 판정 기준 ${w.as_of_kst||kst} KST`,w.sources?.length?w.sources:genericQuoteSources(sym));});
    const defaultSymbol=selectedSymbol&&details[selectedSymbol]?selectedSymbol:(top[0]?.symbol||d.sectors[0]?.etfs?.[0]);selectedSymbol=defaultSymbol;selectedSector=details[defaultSymbol]||d.sectors[0];selectedWatch=watchBySymbol[defaultSymbol]||{symbol:defaultSymbol};renderDetail(selectedSector,selectedWatch);

    $('#events').innerHTML=(d.events||[]).map(e=>`<div class="event"><b>${escapeHtml(e.name)}</b><span>${escapeHtml(e.when)}</span></div>`).join('');if(Array.isArray(meta.cautions)&&meta.cautions.length)$('#cautionList').innerHTML=meta.cautions.map(x=>`<li>${escapeHtml(x)}</li>`).join('');nextBrowserRefreshAt=Date.now()+AUTO_REFRESH_MS;
  }catch(err){console.error(err);$('#freshDot').className='status-dot stale';$('#freshLabel').textContent='데이터 불러오기 실패';$('#dataAge').textContent=err.message;}finally{if(manual){btn.disabled=false;btn.textContent='지금 확인';}}
}
function renderDetail(s,w={}){if(!s)return;const symbol=w.symbol||s.etfs[0];$('#detailTitle').textContent=`${symbol} · ${s.name}`;const factors=[['가격 모멘텀',s.factors.momentum],['상대강도',s.factors.relative_strength],['거래량/수급',s.factors.volume],['Macro 환경',s.factors.macro]];$('#factorBars').innerHTML=factors.map(([n,v])=>`<div class="factor"><div class="factor-name">${n}</div><div class="bar"><div class="fill" style="width:${v}%"></div></div><div class="factor-score">${v}</div></div>`).join('');$('#detailSymbol').textContent=symbol;$('#detailAsOf').textContent=`판정 기준 ${w.as_of_kst||lastData?.meta?.updated_at_kst||'-'} KST`;$('#detailScore').textContent=`${s.score}/100`;$('#detailConclusion').textContent=`${s.status} · ${s.action}`;$('#detailNote').textContent=s.reason||'가격·상대강도·거래량·거시환경을 가중 합산한 규칙 기반 신호입니다.';}
$('#detailQuoteBtn').addEventListener('click',()=>{if(!selectedSymbol)return;openSourceModal(`${selectedSymbol} · 시세/차트`,`판정과 실제 시세를 분리해서 확인합니다.`,selectedWatch?.sources?.length?selectedWatch.sources:genericQuoteSources(selectedSymbol));});
function updateCountdown(){const sec=Math.max(0,Math.ceil((nextBrowserRefreshAt-Date.now())/1000)),m=Math.floor(sec/60),s=sec%60;$('#refreshCountdown').textContent=`화면 자동 확인 ${m}:${String(s).padStart(2,'0')} 후`;if(lastData)updateFreshness(lastData.meta||{},lastData);}
applyUiSettings();
$('#themeToggle').addEventListener('click',()=>{uiTheme=uiTheme==='day'?'night':'day';localStorage.setItem('radarThemeV15',uiTheme);applyUiSettings();});
$('#fontSmaller').addEventListener('click',()=>setFontScale(.9));
$('#fontReset').addEventListener('click',()=>setFontScale(1));
$('#fontLarger').addEventListener('click',()=>setFontScale(1.1));
$('#manualRefresh').addEventListener('click',()=>load({manual:true}));load();setInterval(()=>load(),AUTO_REFRESH_MS);setInterval(updateCountdown,1000);updateCountdown();
