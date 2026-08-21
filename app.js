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

function parseTimestamp(meta={}, d={}) {
  const candidates = [meta.updated_at_iso, meta.updated_at_kst, d.updated_at_kst].filter(Boolean);
  for (const raw of candidates) {
    let text = String(raw).trim();
    if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(text)) text = text.replace(' ', 'T') + ':00+09:00';
    else if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(text)) text += ':00+09:00';
    const t = Date.parse(text);
    if (Number.isFinite(t)) return t;
  }
  return null;
}
function minutesSince(meta={}, d={}) {
  const t = parseTimestamp(meta, d);
  if (t === null) return null;
  return Math.max(0, Math.floor((Date.now() - t) / 60000));
}
function ageText(mins) {
  if (mins === null) return '데이터 경과시간 확인 불가';
  if (mins < 1) return '방금 수집';
  if (mins < 60) return `${mins}분 전 수집`;
  const h = Math.floor(mins / 60), m = mins % 60;
  return `${h}시간 ${m}분 전 수집`;
}
function updateFreshness(meta={}, d={}) {
  const age = minutesSince(meta, d);
  const dot = $('#freshDot');
  dot.className = 'status-dot';
  let label = '수집 시점 확인';
  if (age !== null && age <= 35) { dot.classList.add('fresh'); label = '최근 데이터'; }
  else if (age !== null && age <= 70) { dot.classList.add('warn'); label = '갱신 대기'; }
  else if (age !== null) { dot.classList.add('stale'); label = '오래된 데이터 주의'; }
  else { dot.classList.add('warn'); label = '시간 정보 확인 필요'; }
  $('#freshLabel').textContent = label;
  $('#dataAge').textContent = ageText(age);
}
function escapeHtml(v='') {
  return String(v).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
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

    const kst = meta.updated_at_kst || d.updated_at_kst || '-';
    $('#updatedAt').textContent = `수집 ${kst} KST${meta.updated_at_et ? ` · ${meta.updated_at_et} ET` : ''}`;
    $('#marketViewpoint').textContent = meta.perspective || '세션 정보 업데이트 대기';
    $('#updatePolicy').textContent = meta.update_policy || '서버 데이터는 예약 갱신됩니다.';
    $('#watchAsOf').textContent = `기준 ${kst} KST${meta.perspective ? ` · ${meta.perspective}` : ''}`;
    updateFreshness(meta, d);

    $('#marketStrip').innerHTML = (d.market||[]).map(m => {
      const changeText = m.change_text ?? `${m.change_pct >= 0 ? '+' : ''}${Number(m.change_pct||0).toFixed(2)}%`;
      const body = `<div class="market-name">${escapeHtml(m.name)} <span class="source-mark">↗</span></div><div class="market-value">${escapeHtml(m.value)}</div><div class="market-change ${(m.change_direction ?? m.change_pct) >= 0 ? 'positive' : 'negative'}">${escapeHtml(changeText)}</div><div class="market-source">${escapeHtml(m.source_label || '시세 출처')}</div>`;
      return m.source_url ? `<a class="market-item market-link" href="${m.source_url}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(m.name)} 시세 출처 열기">${body}</a>` : `<div class="market-item">${body}</div>`;
    }).join('');

    $('#regimeName').textContent = d.regime?.name || '-';
    $('#regimeReasons').innerHTML = (d.regime?.reasons||[]).map(x=>`• ${escapeHtml(x)}`).join('<br>');

    $('#macroCards').innerHTML = (d.macro||[]).map(m=> {
      const body = `<div class="macro-top"><div class="macro-title">${escapeHtml(m.name)}${m.source_url ? ' <span class="source-mark">↗</span>' : ''}</div><div class="macro-state ${clsForStatus(m.state)}">${escapeHtml(m.state)}</div></div><div class="macro-sub">${escapeHtml(m.summary)}</div><div class="macro-source">${escapeHtml(m.source_label || '')}</div>`;
      return m.source_url ? `<a class="macro-card panel macro-link" href="${m.source_url}" target="_blank" rel="noopener noreferrer">${body}</a>` : `<div class="macro-card panel">${body}</div>`;
    }).join('');

    $('#sectorTable').innerHTML = (d.sectors||[]).map(s=>`<tr><td class="sector-name">${escapeHtml(s.name)}</td><td><span class="chip ${chipClass(s.status)}">${escapeHtml(s.status)}</span></td><td class="score-cell">${s.score}</td><td><b>${s.etfs.map(escapeHtml).join(' / ')}</b></td><td class="action">${escapeHtml(s.action)}</td></tr>`).join('');
    $('#sectorCards').innerHTML = (d.sectors||[]).map(s=>`<div class="sector-card"><div class="sector-card-top"><div class="sector-name">${escapeHtml(s.name)}</div><span class="chip ${chipClass(s.status)}">${escapeHtml(s.status)}</span></div><div style="margin-top:8px"><b>${s.score}점</b> · ${s.etfs.map(escapeHtml).join(' / ')}</div><small>${escapeHtml(s.action)}</small></div>`).join('');

    const top = d.watchlist || d.sectors.slice().sort((a,b)=>b.score-a.score).slice(0,4);
    const watchBySymbol = Object.fromEntries(top.map(w => [w.symbol || w.etfs?.[0] || '', w]));
    $('#watchList').innerHTML = top.map((w,i)=> {
      const sym = w.symbol || w.etfs?.[0] || '';
      const selected = (selectedSymbol ? selectedSymbol === sym : i===0) ? 'selected' : '';
      const price = w.price_text || (w.price != null ? `$${Number(w.price).toFixed(2)}` : '가격 확인 중');
      const chg = w.change_text || (w.change_pct != null ? `${Number(w.change_pct)>=0?'+':''}${Number(w.change_pct).toFixed(2)}%` : '');
      const chgCls = Number(w.change_pct || 0) >= 0 ? 'positive' : 'negative';
      return `<div class="watch-card ${selected}" data-symbol="${escapeHtml(sym)}">
        <div class="watch-head"><div><div class="symbol">${escapeHtml(sym)}</div><div class="watch-meta">${escapeHtml(w.name||'')}</div></div><span class="chip ${chipClass(w.status)}">${escapeHtml(w.status)}</span></div>
        <div class="watch-price-row"><b>${escapeHtml(price)}</b><span class="${chgCls}">${escapeHtml(chg)}</span></div>
        <div class="watch-date">기준 ${escapeHtml(w.as_of_kst || kst)} KST</div>
        <div class="watch-action">${escapeHtml(w.action)}</div>
        ${w.source_url ? `<a class="watch-source" href="${w.source_url}" target="_blank" rel="noopener noreferrer" data-source-link="1">Yahoo Finance 시세 ↗</a>` : ''}
      </div>`;
    }).join('');

    const details = {};
    (d.sectors||[]).forEach(s => s.etfs.forEach(e => details[e] = s));
    document.querySelectorAll('.watch-card').forEach(card => {
      card.onclick = (ev) => {
        if (ev.target.closest('[data-source-link]')) return;
        selectedSymbol = card.dataset.symbol;
        document.querySelectorAll('.watch-card').forEach(x=>x.classList.remove('selected'));
        card.classList.add('selected');
        renderDetail(details[selectedSymbol] || d.sectors[0], watchBySymbol[selectedSymbol]);
      };
    });
    const defaultSymbol = selectedSymbol && details[selectedSymbol] ? selectedSymbol : (top[0]?.symbol || d.sectors[0]?.etfs?.[0]);
    selectedSymbol = defaultSymbol;
    renderDetail(details[defaultSymbol] || d.sectors[0], watchBySymbol[defaultSymbol]);

    $('#events').innerHTML = (d.events||[]).map(e=>`<div class="event"><b>${escapeHtml(e.name)}</b><span>${escapeHtml(e.when)}</span></div>`).join('');

    if (Array.isArray(meta.cautions) && meta.cautions.length) {
      $('#cautionList').innerHTML = meta.cautions.map(x=>`<li>${escapeHtml(x)}</li>`).join('');
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

function renderDetail(s, w={}) {
  if (!s) return;
  const symbol = w.symbol || s.etfs[0];
  $('#detailTitle').textContent = `${symbol} · ${s.name}`;
  const factors = [['가격 모멘텀',s.factors.momentum],['상대강도',s.factors.relative_strength],['거래량/수급',s.factors.volume],['Macro 환경',s.factors.macro]];
  $('#factorBars').innerHTML = factors.map(([n,v])=>`<div class="factor"><div class="factor-name">${n}</div><div class="bar"><div class="fill" style="width:${v}%"></div></div><div class="factor-score">${v}</div></div>`).join('');
  const priceText = w.price_text || (w.price != null ? `$${Number(w.price).toFixed(2)}` : '가격 확인 중');
  const chgText = w.change_text || (w.change_pct != null ? `${Number(w.change_pct)>=0?'+':''}${Number(w.change_pct).toFixed(2)}%` : '');
  $('#detailPrice').innerHTML = `${escapeHtml(priceText)} <span class="${Number(w.change_pct||0)>=0?'positive':'negative'} detail-change">${escapeHtml(chgText)}</span>`;
  $('#detailAsOf').textContent = `가격 기준 ${w.as_of_kst || lastData?.meta?.updated_at_kst || '-'} KST`;
  const link = $('#detailSource');
  if (w.source_url) { link.href = w.source_url; link.style.display = 'inline-block'; }
  else { link.removeAttribute('href'); link.style.display = 'none'; }
  $('#detailScore').textContent = `${s.score}/100`;
  $('#detailConclusion').textContent = `${s.status} · ${s.action}`;
  $('#detailNote').textContent = s.reason || '가격·상대강도·거래량·거시환경을 가중 합산한 규칙 기반 신호입니다.';
}

function updateCountdown() {
  const sec = Math.max(0, Math.ceil((nextBrowserRefreshAt - Date.now()) / 1000));
  const m = Math.floor(sec/60), s = sec%60;
  $('#refreshCountdown').textContent = `화면 자동 확인 ${m}:${String(s).padStart(2,'0')} 후`;
  if (lastData) updateFreshness(lastData.meta || {}, lastData);
}

$('#manualRefresh').addEventListener('click',()=>load({manual:true}));
load();
setInterval(()=>load(), AUTO_REFRESH_MS);
setInterval(updateCountdown, 1000);
updateCountdown();
