const $ = (s) => document.querySelector(s);
const AUTO_REFRESH_MS = 5 * 60 * 1000;
let lastData = null;
let selectedSymbol = null;
let nextBrowserRefreshAt = Date.now() + AUTO_REFRESH_MS;

const clsForStatus = (s='') => {
  if (s.includes('상승') || s.includes('강함') || s.includes('개선')) return 'state-good';
  if (s.includes('약화') || s.includes('부담') || s.includes('위험')) return 'state-bad';
  if (s.includes('초기') || s.includes('주의') || s.includes('냉각')) return 'state-warn';
  return 'state-mid';
};
const chipClass = (s='') => s.includes('상승') ? 'upcycle' : s.includes('초기') ? 'early' : s.includes('약화') ? 'weak' : 'neutral';

function minutesSince(iso) {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  return Math.max(0, Math.floor((Date.now() - t) / 60000));
}
function ageText(mins) {
  if (mins === null) return '데이터 경과시간 확인 불가';
  if (mins < 1) return '방금 수집';
  if (mins < 60) return `${mins}분 전 수집`;
  const h = Math.floor(mins / 60), m = mins % 60;
  return `${h}시간 ${m}분 전 수집`;
}
function updateFreshness(meta={}) {
  const age = minutesSince(meta.updated_at_iso);
  const dot = $('#freshDot');
  dot.className = 'status-dot';
  let label = '수집 시점 확인';
  if (age !== null && age <= 40) { dot.classList.add('fresh'); label = '최근 서버 데이터'; }
  else if (age !== null && age <= 90) { dot.classList.add('warn'); label = '데이터 갱신 대기 가능'; }
  else { dot.classList.add('stale'); label = '오래된 데이터 주의'; }
  $('#freshLabel').textContent = label;
  $('#dataAge').textContent = ageText(age);
}

async function load({manual=false}={}) {
  const btn = $('#manualRefresh');
  if (manual) { btn.disabled = true; btn.textContent = '확인 중…'; }
  try {
    const r = await fetch(`data/dashboard.json?t=${Date.now()}`, {cache:'no-store'});
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    lastData = d;
    const meta = d.meta || {};

    $('#updatedAt').textContent = `수집 ${meta.updated_at_kst || d.updated_at_kst || '-'} KST${meta.updated_at_et ? ` · ${meta.updated_at_et} ET` : ''}`;
    $('#marketViewpoint').textContent = meta.perspective || '데이터 수집 시점 기준 관점';
    $('#updatePolicy').textContent = meta.update_policy || '서버 데이터는 예약 갱신됩니다.';
    updateFreshness(meta);

    $('#marketStrip').innerHTML = (d.market||[]).map(m => {
      const changeText = m.change_text ?? `${m.change_pct >= 0 ? '+' : ''}${Number(m.change_pct||0).toFixed(2)}%`;
      return `<div class="market-item"><div class="market-name">${m.name}</div><div class="market-value">${m.value}</div><div class="market-change ${(m.change_direction ?? m.change_pct) >= 0 ? 'positive' : 'negative'}">${changeText}</div></div>`;
    }).join('');

    $('#regimeName').textContent = d.regime?.name || '-';
    $('#regimeReasons').innerHTML = (d.regime?.reasons||[]).map(x=>`• ${x}`).join('<br>');

    $('#macroCards').innerHTML = (d.macro||[]).map(m=>`<div class="macro-card panel"><div class="macro-top"><div class="macro-title">${m.name}</div><div class="macro-state ${clsForStatus(m.state)}">${m.state}</div></div><div class="macro-sub">${m.summary}</div></div>`).join('');

    $('#sectorTable').innerHTML = (d.sectors||[]).map(s=>`<tr><td class="sector-name">${s.name}</td><td><span class="chip ${chipClass(s.status)}">${s.status}</span></td><td class="score-cell">${s.score}</td><td><b>${s.etfs.join(' / ')}</b></td><td class="action">${s.action}</td></tr>`).join('');
    $('#sectorCards').innerHTML = (d.sectors||[]).map(s=>`<div class="sector-card"><div class="sector-card-top"><div class="sector-name">${s.name}</div><span class="chip ${chipClass(s.status)}">${s.status}</span></div><div style="margin-top:8px"><b>${s.score}점</b> · ${s.etfs.join(' / ')}</div><small>${s.action}</small></div>`).join('');

    const top = d.watchlist || d.sectors.slice().sort((a,b)=>b.score-a.score).slice(0,4);
    $('#watchList').innerHTML = top.map((w,i)=>`<div class="watch-card ${(selectedSymbol ? selectedSymbol === (w.symbol||w.etfs?.[0]) : i===0) ? 'selected':''}" data-symbol="${w.symbol||w.etfs?.[0]||''}"><div class="watch-head"><div><div class="symbol">${w.symbol||w.etfs?.[0]||'-'}</div><div class="watch-meta">${w.name||''}</div></div><span class="chip ${chipClass(w.status)}">${w.status}</span></div><div class="watch-action">${w.action}</div></div>`).join('');

    const details = {};
    d.sectors.forEach(s => s.etfs.forEach(e => details[e] = s));
    document.querySelectorAll('.watch-card').forEach(card => {
      card.onclick = () => {
        selectedSymbol = card.dataset.symbol;
        document.querySelectorAll('.watch-card').forEach(x=>x.classList.remove('selected'));
        card.classList.add('selected');
        renderDetail(details[selectedSymbol] || d.sectors[0]);
      };
    });
    const defaultSymbol = selectedSymbol && details[selectedSymbol] ? selectedSymbol : (top[0]?.symbol || d.sectors[0]?.etfs?.[0]);
    selectedSymbol = defaultSymbol;
    renderDetail(details[defaultSymbol] || d.sectors[0]);

    $('#events').innerHTML = (d.events||[]).map(e=>`<div class="event"><b>${e.name}</b><span>${e.when}</span></div>`).join('');

    if (Array.isArray(meta.cautions) && meta.cautions.length) {
      $('#cautionList').innerHTML = meta.cautions.map(x=>`<li>${x}</li>`).join('');
    }
    nextBrowserRefreshAt = Date.now() + AUTO_REFRESH_MS;
  } catch(err) {
    console.error(err);
    $('#freshDot').className = 'status-dot stale';
    $('#freshLabel').textContent = '데이터 불러오기 실패';
    $('#dataAge').textContent = err.message;
  } finally {
    if (manual) { btn.disabled = false; btn.textContent = '지금 확인'; }
  }
}

function renderDetail(s) {
  if (!s) return;
  $('#detailTitle').textContent = `${s.etfs[0]} · ${s.name}`;
  const factors = [['가격 모멘텀',s.factors.momentum],['상대강도',s.factors.relative_strength],['거래량/수급',s.factors.volume],['Macro 환경',s.factors.macro]];
  $('#factorBars').innerHTML = factors.map(([n,v])=>`<div class="factor"><div class="factor-name">${n}</div><div class="bar"><div class="fill" style="width:${v}%"></div></div><div class="factor-score">${v}</div></div>`).join('');
  $('#detailScore').textContent = `${s.score}/100`;
  $('#detailConclusion').textContent = `${s.status} · ${s.action}`;
  $('#detailNote').textContent = s.reason || '가격·상대강도·거래량·거시환경을 가중 합산한 규칙 기반 신호입니다.';
}

function updateCountdown() {
  const sec = Math.max(0, Math.ceil((nextBrowserRefreshAt - Date.now()) / 1000));
  const m = Math.floor(sec/60), s = sec%60;
  $('#refreshCountdown').textContent = `화면 자동 확인 ${m}:${String(s).padStart(2,'0')} 후`;
  if (lastData?.meta) updateFreshness(lastData.meta);
}

$('#manualRefresh').addEventListener('click',()=>load({manual:true}));
load();
setInterval(()=>load(), AUTO_REFRESH_MS);
setInterval(updateCountdown, 1000);
updateCountdown();
