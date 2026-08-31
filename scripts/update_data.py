from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote

import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "dashboard.json"
HISTORY = ROOT / "data" / "history.json"
ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
UTC = ZoneInfo("UTC")

MARKET = {
    "나스닥 선물": "NQ=F",
    "S&P500 선물": "ES=F",
    "SOX 지수": "^SOX",
    "WTI 원유": "CL=F",
    "미 10년물": "^TNX",
    "DXY": "DX-Y.NYB",
    "비트코인": "BTC-USD",
    "달러/원": "KRW=X",
    "VIX": "^VIX",
}

SECTORS = [
    ("반도체/AI 하드웨어", ["SMH", "SOXX"]),
    ("바이오/헬스케어", ["XBI", "IBB"]),
    ("산업재", ["XLI"]),
    ("소재/원자재", ["XLB"]),
    ("에너지", ["XLE"]),
    ("금융", ["XLF"]),
    ("금/금광", ["GLD", "GDX"]),
    ("빅테크", ["QQQM"]),
    ("필수소비재", ["XLP"]),
    ("리츠", ["VNQ"]),
]
BASE = "SPY"

BASE_FACTOR_WEIGHTS = {
    "momentum": 0.30,
    "relative_strength": 0.30,
    "volume": 0.15,
    "breadth": 0.10,
    "macro": 0.15,
}

# Representative ETF baskets used only as a free sector-participation proxy.
# This is intentionally not presented as constituent-level advance/decline breadth.
VALIDATION_ETFS = {
    "반도체/AI 하드웨어": ["SMH", "SOXX", "XSD"],
    "바이오/헬스케어": ["XBI", "IBB", "XLV"],
    "산업재": ["XLI", "PAVE", "ITA"],
    "소재/원자재": ["XLB", "VAW", "PICK"],
    "에너지": ["XLE", "XOP", "OIH"],
    "금융": ["XLF", "KRE", "KBE"],
    "금/금광": ["GLD", "GDX", "GDXJ"],
    "빅테크": ["QQQM", "XLK", "IGV"],
    "필수소비재": ["XLP", "VDC", "FSTA"],
    "리츠": ["VNQ", "XLRE", "SCHH"],
}

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; cycle-radar/1.14; +https://github.com/)"}


def load_previous_dashboard():
    if not OUT.exists():
        return {}
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"previous dashboard warning: {e}")
        return {}


def telegram_send(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("telegram: secrets not configured; skip")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=20)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(data)
        print("telegram: message sent")
        return True
    except Exception as e:
        print(f"telegram warning: {e}")
        return False


def telegram_test_message(now_kst):
    if os.getenv("TELEGRAM_TEST", "false").lower() not in {"1", "true", "yes", "on"}:
        return False
    dashboard_url = os.getenv("DASHBOARD_URL", "").strip()
    lines = [
        "✅ 미국 산업 사이클 레이더 알림 설정 완료",
        "",
        "🔔 현재 알림 조건",
        "1) 산업별 판단이 새로 ‘소액 진입 검토’로 변경될 때",
        "2) 산업별 판단이 새로 ‘분할 접근’으로 변경될 때",
        "3) 시장 타이밍 판단이 새로 ‘분할 접근 검토’로 변경될 때",
        "4) 시장 타이밍 판단이 새로 ‘1차 분할매수 후보’로 변경될 때",
        "",
        "• 동일 상태 유지 시 중복 알림 없음",
        "• 동일 유형 재알림은 18시간 쿨다운",
        "• 신뢰도 80점 상향 돌파 / 급격한 약화도 품질 알림",
        "• 관찰 / 조정 대기 / 추가매수 보류 자체는 매수 알림 대상 아님",
        "",
        f"확인 시각: {now_kst.strftime('%Y-%m-%d %H:%M')} KST",
    ]
    if dashboard_url:
        lines += ["", f"대시보드: {dashboard_url}"]
    return telegram_send("\n".join(lines))


def alert_allowed(history, key, now_kst, cooldown_hours=18):
    state = history.setdefault("alert_state", {}) if isinstance(history, dict) else {}
    last = state.get(key)
    if last:
        try:
            dt = datetime.fromisoformat(last)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=KST)
            if (now_kst - dt.astimezone(KST)).total_seconds() < cooldown_hours * 3600:
                return False
        except Exception:
            pass
    return True


def mark_alert(history, key, now_kst):
    if isinstance(history, dict):
        history.setdefault("alert_state", {})[key] = now_kst.isoformat(timespec="seconds")


def notify_new_signals(previous, sectors, timing, now_kst, history=None):
    """Send meaningful signal transitions with cooldown, not every scheduled run."""
    if os.getenv("TELEGRAM_TEST", "false").lower() in {"1", "true", "yes", "on"}:
        return

    prev_sectors = {
        s.get("name"): s for s in (previous.get("sectors") or []) if isinstance(s, dict)
    }
    alerts = []
    signal_names = set()

    for s in sectors:
        name = s.get("name", "산업")
        current_action = s.get("action")
        prev_obj = prev_sectors.get(name) or {}
        prev_action = prev_obj.get("action")
        sector_targets = {"소액 진입 검토", "분할 접근"}
        if current_action in sector_targets and current_action != prev_action:
            alerts.append({
                "kind": "sector", "key": f"sector:{name}:{current_action}",
                "name": name, "etfs": " / ".join(s.get("etfs") or []),
                "action": current_action, "score": s.get("score"),
                "raw_score": s.get("raw_score"), "status": s.get("status"),
                "previous": prev_action or "이전 기록 없음", "reason": s.get("reason", ""),
                "metrics": s.get("metrics") or {}, "trend": s.get("trend") or {},
                "confidence": s.get("confidence") or {}, "rank": s.get("rank"),
                "rank_change": s.get("rank_change"), "flags": s.get("flags") or [],
                "buy_signal_days": s.get("buy_signal_days", 0), "stage_days": s.get("stage_days", 0),
                "signal_story": s.get("signal_story", ""),
            })
            signal_names.add(name)
        elif prev_action in sector_targets and current_action not in sector_targets:
            alerts.append({
                "kind": "sector_exit", "key": f"sector_exit:{name}",
                "name": name, "etfs": " / ".join(s.get("etfs") or []),
                "action": current_action or "관찰", "score": s.get("score"),
                "raw_score": s.get("raw_score"), "status": s.get("status"),
                "previous": prev_action, "reason": s.get("reason", ""),
                "metrics": s.get("metrics") or {}, "trend": s.get("trend") or {},
                "confidence": s.get("confidence") or {}, "rank": s.get("rank"),
                "rank_change": s.get("rank_change"), "flags": s.get("flags") or [],
                "buy_signal_days": s.get("buy_signal_days", 0), "stage_days": s.get("stage_days", 0),
                "signal_story": s.get("signal_story", ""),
            })

        confidence = float((s.get("confidence") or {}).get("score") or 0)
        prev_confidence = float((prev_obj.get("confidence") or {}).get("score") or 0)
        if name not in signal_names and current_action in sector_targets and confidence >= 80 > prev_confidence:
            alerts.append({
                "kind": "quality", "key": f"quality:{name}", "name": name,
                "etfs": " / ".join(s.get("etfs") or []), "action": current_action,
                "score": s.get("score"), "confidence": s.get("confidence") or {},
                "trend": s.get("trend") or {}, "flags": s.get("flags") or [],
                "buy_signal_days": s.get("buy_signal_days", 0), "stage_days": s.get("stage_days", 0),
                "signal_story": s.get("signal_story", ""),
            })

        trend_label = (s.get("trend") or {}).get("label")
        prev_trend = (prev_obj.get("trend") or {}).get("label")
        if trend_label == "↓ 급격한 약화" and prev_trend != trend_label and float(s.get("score") or 0) >= 55:
            alerts.append({
                "kind": "weakening", "key": f"weakening:{name}", "name": name,
                "etfs": " / ".join(s.get("etfs") or []), "action": current_action,
                "score": s.get("score"), "raw_score": s.get("raw_score"),
                "trend": s.get("trend") or {}, "confidence": s.get("confidence") or {},
                "flags": s.get("flags") or [],
                "buy_signal_days": s.get("buy_signal_days", 0), "stage_days": s.get("stage_days", 0),
                "signal_story": s.get("signal_story", ""),
            })

    current_timing = timing.get("action") if isinstance(timing, dict) else None
    prev_timing = ((previous.get("timing") or {}).get("action") if isinstance(previous, dict) else None)
    timing_targets = {"분할 접근 검토", "1차 분할매수 후보"}
    if current_timing in timing_targets and current_timing != prev_timing:
        alerts.append({
            "kind": "timing", "key": f"timing:{current_timing}",
            "action": current_timing, "previous": prev_timing or "이전 기록 없음",
            "low_buy": (timing.get("low_buy") or {}).get("score"),
            "reversal": (timing.get("reversal") or {}).get("score"),
            "summary": timing.get("summary", ""),
        })
    elif prev_timing in timing_targets and current_timing not in timing_targets:
        alerts.append({
            "kind": "timing_exit", "key": "timing_exit",
            "action": current_timing or "신호 확인 대기", "previous": prev_timing,
            "low_buy": (timing.get("low_buy") or {}).get("score"),
            "reversal": (timing.get("reversal") or {}).get("score"),
            "summary": timing.get("summary", ""),
        })

    if not alerts:
        print("telegram: no new meaningful signal")
        return

    dashboard_url = os.getenv("DASHBOARD_URL", "").strip()
    for a in alerts:
        key = a.get("key", a.get("kind", "alert"))
        if not alert_allowed(history or {}, key, now_kst):
            print(f"telegram: cooldown skip {key}")
            continue

        if a["kind"] == "sector":
            conf = a.get("confidence") or {}
            tr = a.get("trend") or {}
            lines = [
                "🚨 미국 산업 사이클 레이더", "",
                f"{'🟡' if a['action'] == '소액 진입 검토' else '🟢'} {a['name']}  {a['etfs']}",
                f"오늘의 판단: {a['action']}",
                f"안정화 레이더: {a['score']}점 · 원점수 {a.get('raw_score', '-')}점",
                f"추세: {tr.get('label', '-')} · 3일 {tr.get('delta_3d', 0):+}점",
                f"신뢰도: {conf.get('score', '-')}점 ({conf.get('grade', '-')}) · 순위 #{a.get('rank') or '-'}",
                f"신호 흐름: {a.get('signal_story') or '오늘 판단 변경'}",
                f"이전 판단: {a['previous']}",
            ]
            m = a.get("metrics") or {}
            if m.get("session") in {"premarket", "afterhours"}:
                label = "프리마켓" if m.get("session") == "premarket" else "애프터마켓"
                lines += ["", f"🌙 {label}: {float(m.get('extended_change_pct') or 0):+.2f}%",
                          f"SPY 대비: {float(m.get('extended_vs_spy_pctp') or 0):+.2f}%p · 보정 {float(m.get('extended_score_adjustment') or 0):+.1f}점"]
            if a.get("flags"):
                lines += ["", "주의: " + " · ".join(a["flags"][:3])]
        elif a["kind"] == "sector_exit":
            tr = a.get("trend") or {}; conf = a.get("confidence") or {}
            score = a.get('score', '-')
            reasons = []
            if tr.get('label'): reasons.append(f"추세 {tr.get('label')}")
            delta3 = tr.get('delta_3d')
            if delta3 is not None: reasons.append(f"3일 {delta3:+}점")
            peak = tr.get('peak_drop_5d')
            if peak is not None: reasons.append(f"고점 대비 {peak:+}점")
            if a.get('flags'): reasons.append(" / ".join((a.get('flags') or [])[:2]))
            if not reasons and a.get('reason'): reasons.append(a['reason'])
            lines = [
                "⚪ 매수형 신호 종료", "",
                f"{a['name']}  {a['etfs']}",
                f"이전 판단: {a['previous']} → 현재 {a['action']}",
                f"신호 흐름: {a.get('signal_story') or ('⚪ 오늘 매수 신호 종료 · ' + str(a['action']) + ' 전환')}",
                f"안정화 레이더: {score}점 · 신뢰도 {conf.get('score', '-')}점 ({conf.get('grade', '-')})",
                f"종료 사유: {' · '.join(reasons) if reasons else '추세 약화 또는 상태 이탈'}",
            ]
        elif a["kind"] == "quality":
            conf = a.get("confidence") or {}; tr = a.get("trend") or {}
            lines = ["🔵 신호 신뢰도 강화", "", f"{a['name']}  {a['etfs']}",
                     f"판단: {a['action']} · 레이더 {a['score']}점",
                     f"신뢰도 {conf.get('score', '-')}점 ({conf.get('grade', '-')})",
                     f"추세: {tr.get('label', '-')}"]
        elif a["kind"] == "weakening":
            tr = a.get("trend") or {}; conf = a.get("confidence") or {}
            lines = ["🔴 레이더 급격한 약화", "", f"{a['name']}  {a['etfs']}",
                     f"현재 판단: {a['action']} · 안정화 {a['score']}점 / 원점수 {a.get('raw_score', '-')}점",
                     f"최근 3일 변화: {tr.get('delta_3d', 0):+}점 · 고점 대비 {tr.get('peak_drop_5d', 0):+}점",
                     f"신뢰도: {conf.get('score', '-')}점 ({conf.get('grade', '-')})"]
        elif a["kind"] == "timing_exit":
            lines = ["⚪ 시장 타이밍 신호 종료", "", f"이전 판단: {a['previous']} → 현재 {a['action']}",
                     f"저점매수 매력도: {a['low_buy']}점", f"반전 확인도: {a['reversal']}점"]
            if a.get("summary"):
                lines += ["", "종료 사유: " + a["summary"]]
        else:
            lines = ["🚨 미국 시장 타이밍 신호", "", f"판단: {a['action']}",
                     f"저점매수 매력도: {a['low_buy']}점", f"반전 확인도: {a['reversal']}점",
                     f"이전 판단: {a['previous']}"]
            if a.get("summary"):
                lines += ["", a["summary"]]

        lines += ["", f"{now_kst.strftime('%Y-%m-%d %H:%M')} KST",
                  "알림: 신규 진입 / 신호 종료 / 신뢰도 강화 / 급격한 약화 · 동일 유형 18시간 쿨다운"]
        if dashboard_url:
            lines += [f"대시보드: {dashboard_url}"]
        if telegram_send("\n".join(lines)):
            mark_alert(history or {}, key, now_kst)


def sanitize_json_value(value):
    """Convert NaN/Infinity and numpy/pandas scalars into strict JSON-safe values."""
    if isinstance(value, dict):
        return {str(k): sanitize_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json_value(v) for v in value]
    # numpy/pandas scalar -> Python scalar
    if hasattr(value, "item") and not isinstance(value, (str, bytes, bytearray)):
        try:
            value = value.item()
        except Exception:
            pass
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def write_json_atomic(path, data):
    """Write strict JSON atomically so a partial/invalid dashboard is never published."""
    safe = sanitize_json_value(data)
    payload = json.dumps(safe, ensure_ascii=False, indent=2, allow_nan=False)
    # Parse once before replacement as an additional integrity check.
    json.loads(payload)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def clamp(x, lo=0, hi=100):
    return max(lo, min(hi, x))


def round_int(x):
    return int(round(float(x)))


def yahoo_url(symbol):
    return f"https://finance.yahoo.com/quote/{quote(symbol, safe='')}"


def fred_url(series):
    return f"https://fred.stlouisfed.org/series/{series}"


def etf_sources(symbol):
    return [
        {"label": "Yahoo Finance", "note": f"{symbol} 시세·차트", "url": yahoo_url(symbol)},
        {"label": "Nasdaq", "note": f"{symbol} ETF/시세 검색", "url": f"https://www.nasdaq.com/market-activity/etf/{symbol.lower()}"},
        {"label": "Investing.com", "note": f"{symbol} 검색", "url": f"https://www.investing.com/search/?q={quote(symbol)}"},
        {"label": "Bloomberg", "note": f"{symbol}:US 시세", "url": f"https://www.bloomberg.com/quote/{symbol}:US"},
    ]


def market_sources(name, symbol):
    official = {
        "나스닥 선물": ("CME Group", "E-mini Nasdaq-100 Futures", "https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.html"),
        "S&P500 선물": ("CME Group", "E-mini S&P 500 Futures", "https://www.cmegroup.com/markets/equities/sp/e-mini-sandp500.html"),
        "SOX 지수": ("Nasdaq", "PHLX Semiconductor Sector Index", "https://www.nasdaq.com/market-activity/index/sox"),
        "WTI 원유": ("CME Group", "WTI Crude Oil Futures", "https://www.cmegroup.com/markets/energy/crude-oil/light-sweet-crude.html"),
        "미 10년물": ("FRED", "10-Year Treasury Rate", fred_url("DGS10")),
        "DXY": ("ICE", "U.S. Dollar Index Futures", "https://www.ice.com/products/194/US-Dollar-Index-Futures"),
        "비트코인": ("Coinbase", "BTC-USD 시장", "https://www.coinbase.com/price/bitcoin"),
        "달러/원": ("Investing.com", "USD/KRW 시세", "https://www.investing.com/currencies/usd-krw"),
    }
    arr = []
    if name in official:
        label, note, url = official[name]
        arr.append({"label": label, "note": note, "url": url})
    arr.append({"label": "Yahoo Finance", "note": f"수집에 사용한 무료 시세 ({symbol})", "url": yahoo_url(symbol)})
    return arr


def daily_history(symbol, period="6mo"):
    df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
    if df is None or df.empty:
        raise RuntimeError(f"No daily data for {symbol}")
    return df


def intraday_history(symbol):
    try:
        df = yf.Ticker(symbol).history(period="1d", interval="15m", prepost=True, auto_adjust=True)
        return df if df is not None else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def latest_extended(symbol):
    t = yf.Ticker(symbol)
    intr = t.history(period="1d", interval="5m", prepost=True, auto_adjust=True)
    daily = t.history(period="5d", interval="1d", auto_adjust=True)
    if daily is None or daily.empty:
        return None, None, 0.0
    prev = float(daily["Close"].iloc[-2] if len(daily) >= 2 else daily["Close"].iloc[-1])
    latest = float(intr["Close"].dropna().iloc[-1]) if intr is not None and not intr.empty else float(daily["Close"].iloc[-1])
    chg = (latest / prev - 1) * 100 if prev else 0.0
    return latest, prev, chg


def extended_snapshot(symbol, session):
    """Return the latest quote plus the move specifically outside the regular session.

    In pre/after-hours, regular_close is the most recent official daily close.
    During the regular session, extended_change_pct is 0 and the live price is used
    directly by the normal momentum calculation.
    """
    t = yf.Ticker(symbol)
    intr = t.history(period="1d", interval="5m", prepost=True, auto_adjust=True)
    daily = t.history(period="5d", interval="1d", auto_adjust=True)
    if daily is None or daily.empty:
        return {
            "latest": None,
            "regular_close": None,
            "prev_close": None,
            "day_change_pct": 0.0,
            "extended_change_pct": 0.0,
        }

    closes = daily["Close"].dropna()
    regular_close = float(closes.iloc[-1])
    prev_close = float(closes.iloc[-2] if len(closes) >= 2 else closes.iloc[-1])
    latest = (
        float(intr["Close"].dropna().iloc[-1])
        if intr is not None and not intr.empty and not intr["Close"].dropna().empty
        else regular_close
    )
    day_change = (latest / prev_close - 1) * 100 if prev_close else 0.0
    ext_change = (
        (latest / regular_close - 1) * 100
        if session in {"premarket", "afterhours"} and regular_close
        else 0.0
    )
    return {
        "latest": latest,
        "regular_close": regular_close,
        "prev_close": prev_close,
        "day_change_pct": day_change,
        "extended_change_pct": ext_change,
    }


def spark_values(symbol, max_points=28):
    df = intraday_history(symbol)
    if df.empty or "Close" not in df:
        return []
    vals = [round(float(x), 6) for x in df["Close"].dropna().tail(max_points).tolist()]
    return vals


def fred_series(series_id):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    from io import StringIO
    df = pd.read_csv(StringIO(r.text))
    df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna()


def tnx_yield(value):
    if value is None:
        return None
    v = float(value)
    # Yahoo ^TNX is typically 10x the yield (e.g. 46.9 => 4.69),
    # but some upstream paths can already return ~4.69.
    return v / 10.0 if v > 15 else v


def market_session(now_et):
    if now_et.weekday() >= 5:
        return "closed", "주말 · 직전 미국장 최종 데이터 관점"
    mins = now_et.hour * 60 + now_et.minute
    if 4 * 60 <= mins < 9 * 60 + 30:
        return "premarket", "미국장 프리마켓 관점"
    if 9 * 60 + 30 <= mins < 16 * 60:
        return "regular", "미국장 정규장 진행 중 관점"
    if 16 * 60 <= mins < 20 * 60:
        return "afterhours", "미국장 애프터마켓 관점"
    return "closed", "미국장 마감 후 최종 데이터 관점"


def market_breadth_score(spy_df, spy_ext_price=None):
    """Free breadth proxy: equal-weight vs cap-weight participation in S&P 500 and Nasdaq-100."""
    spy = spy_df["Close"].dropna().astype(float).copy()
    if spy_ext_price is not None and len(spy):
        spy.iloc[-1] = float(spy_ext_price)

    pairs = [("RSP", "SPY"), ("QQQE", "QQQ")]
    components = []
    raw = {}
    for equal_ticker, cap_ticker in pairs:
        try:
            eq = daily_history(equal_ticker, "6mo")["Close"].dropna().astype(float).copy()
            cap = spy.copy() if cap_ticker == "SPY" else daily_history(cap_ticker, "6mo")["Close"].dropna().astype(float).copy()
            eq_ext, _, _ = latest_extended(equal_ticker)
            cap_ext = spy_ext_price if cap_ticker == "SPY" else latest_extended(cap_ticker)[0]
            if eq_ext is not None and len(eq): eq.iloc[-1] = float(eq_ext)
            if cap_ext is not None and len(cap): cap.iloc[-1] = float(cap_ext)
            if len(eq) > 21 and len(cap) > 21:
                eq5 = (float(eq.iloc[-1]) / float(eq.iloc[-6]) - 1) * 100
                cap5 = (float(cap.iloc[-1]) / float(cap.iloc[-6]) - 1) * 100
                eq20 = (float(eq.iloc[-1]) / float(eq.iloc[-21]) - 1) * 100
                cap20 = (float(cap.iloc[-1]) / float(cap.iloc[-21]) - 1) * 100
                rs5, rs20 = eq5-cap5, eq20-cap20
                score = clamp(50 + rs5 * 6 + rs20 * 3)
                components.append(score)
                raw[f"{equal_ticker}_vs_{cap_ticker}_5d"] = round(rs5, 2)
                raw[f"{equal_ticker}_vs_{cap_ticker}_20d"] = round(rs20, 2)
        except Exception as e:
            print(f"breadth {equal_ticker}/{cap_ticker} warning: {e}")
    score = round(sum(components) / len(components), 1) if components else 50.0
    return score, raw


def market_regime_profile(spy_df, spy_ext_price, vix, breadth_score, env_score):
    close = spy_df["Close"].dropna().astype(float).copy()
    if spy_ext_price is not None and len(close): close.iloc[-1] = float(spy_ext_price)
    current = float(close.iloc[-1])
    ma50 = float(close.tail(50).mean()) if len(close) >= 50 else current
    ma200 = float(close.tail(200).mean()) if len(close) >= 200 else ma50
    above50 = (current / ma50 - 1) * 100 if ma50 else 0
    above200 = (current / ma200 - 1) * 100 if ma200 else 0
    trend_score = clamp(50 + above50 * 4 + above200 * 2 + (10 if ma50 >= ma200 else -10))
    vix_score = 88 if vix < 16 else 75 if vix < 20 else 58 if vix < 24 else 38 if vix < 30 else 18
    score = round_int(trend_score * .35 + float(breadth_score) * .25 + float(env_score) * .20 + vix_score * .20)
    if score >= 65:
        label, guidance = "Risk-on", "시장 추세와 참여도가 비교적 우호적입니다. 섹터 강도 신호를 정상 해석합니다."
    elif score >= 45:
        label, guidance = "Neutral", "시장 방향성이 혼재합니다. 섹터별 추세·신뢰도를 함께 확인합니다."
    else:
        label, guidance = "Risk-off", "시장 전체 위험회피가 우세합니다. 매수형 섹터 신호를 한 단계 보수적으로 해석합니다."
    return {"label": label, "score": score, "guidance": guidance,
            "metrics": {"spy_vs_ma50_pct": round(above50,2), "spy_vs_ma200_pct": round(above200,2),
                        "trend_score": round_int(trend_score), "breadth_score": round(float(breadth_score),1),
                        "environment_score": round_int(env_score), "vix": round(float(vix),2)}}


def sector_participation_score(name, spy_df):
    basket = VALIDATION_ETFS.get(name, [])
    spy = spy_df["Close"].dropna().astype(float)
    spy5 = (float(spy.iloc[-1])/float(spy.iloc[-6])-1)*100 if len(spy)>6 else 0.0
    values, details, failures = [], [], []
    for sym in basket:
        try:
            c = daily_history(sym, "3mo")["Close"].dropna().astype(float)
            if len(c) <= 6: raise RuntimeError("history too short")
            r5 = (float(c.iloc[-1])/float(c.iloc[-6])-1)*100
            rs5 = r5-spy5
            values.append((r5,rs5)); details.append({"symbol":sym,"return_5d_pct":round(r5,2),"rs5_pctp":round(rs5,2)})
        except Exception as e:
            failures.append(sym); print(f"participation {name}/{sym} warning: {e}")
    if not values:
        return {"score":50,"positive_pct":None,"outperform_pct":None,"members":details,"failures":failures}
    positive = sum(1 for r,rs in values if r>0)/len(values)*100
    outperform = sum(1 for r,rs in values if rs>0)/len(values)*100
    avg_rs = sum(rs for r,rs in values)/len(values)
    rs_component = clamp(50+avg_rs*8)
    score = round_int(clamp(positive*.40+outperform*.40+rs_component*.20))
    return {"score":score,"positive_pct":round(positive,1),"outperform_pct":round(outperform,1),
            "avg_rs5_pctp":round(avg_rs,2),"members":details,"failures":failures}


def event_risk_profile(events, now_kst):
    upcoming=[]
    for e in events or []:
        try:
            dt=datetime.fromisoformat(e.get("iso_kst"))
            if dt.tzinfo is None: dt=dt.replace(tzinfo=KST)
            hours=(dt.astimezone(KST)-now_kst).total_seconds()/3600
            if 0<=hours<=36 and int(e.get("impact",1))>=2:
                upcoming.append((hours,e))
        except Exception:
            continue
    if not upcoming:
        return {"active":False,"penalty":0,"label":"없음","event":None}
    hours,e=min(upcoming,key=lambda x:x[0])
    impact=int(e.get("impact",1)); penalty=10 if impact>=3 and hours<=24 else 7 if impact>=3 else 5
    return {"active":True,"penalty":penalty,"label":f"{e.get('name')} {hours:.1f}시간 전", "event":e}


def calc_sector(primary, spy_df, spy_ext_price=None, ten_y_change=0, vix=20, breadth_score=50, session="closed", factor_weights=None):
    df = daily_history(primary, "6mo")
    close = df["Close"].dropna().astype(float).copy()
    vol = df["Volume"].fillna(0).astype(float)
    sector_snap = extended_snapshot(primary, session)
    ext = sector_snap.get("latest")
    extchg = sector_snap.get("day_change_pct", 0.0)
    ext_session_chg = sector_snap.get("extended_change_pct", 0.0)
    if session == "regular" and ext is not None and len(close): close.iloc[-1] = ext

    spy = spy_df["Close"].dropna().astype(float).copy()
    spy_snap = extended_snapshot(BASE, session)
    if session == "regular" and spy_ext_price is not None and len(spy): spy.iloc[-1] = spy_ext_price

    r5 = (close.iloc[-1]/close.iloc[-6]-1)*100 if len(close)>6 else 0
    r20 = (close.iloc[-1]/close.iloc[-21]-1)*100 if len(close)>21 else 0
    spy5 = (spy.iloc[-1]/spy.iloc[-6]-1)*100 if len(spy)>6 else 0
    spy20 = (spy.iloc[-1]/spy.iloc[-21]-1)*100 if len(spy)>21 else 0
    rs5, rs20 = r5-spy5, r20-spy20
    rs_accel = rs5-rs20

    returns = close.pct_change().dropna()*100
    daily_vol_pct = float(returns.tail(20).std()) if len(returns)>=5 else 1.0
    daily_vol_pct = max(daily_vol_pct, .15)
    z5 = r5/(daily_vol_pct*math.sqrt(5))
    z20 = r20/(daily_vol_pct*math.sqrt(20))
    raw_mom = clamp(50+r5*3+r20*1.3)
    vol_adj_mom = clamp(50+z5*16+z20*8)
    mom = clamp(raw_mom*.60+vol_adj_mom*.40)
    rel = clamp(50+rs20*4+rs_accel*3)

    v_recent=vol.tail(5).mean(); prior=vol.iloc[-25:-5] if len(vol)>=25 else vol.head(max(0,len(vol)-5))
    v_base=prior.mean() if len(prior) else max(v_recent,1); vr=v_recent/v_base if v_base else 1
    direction_bonus=10 if r5>0 else (-10 if r5<-2 else 0)
    volume=clamp(50+(vr-1)*70+direction_bonus); breadth=clamp(float(breadth_score))

    macro=55
    if primary in {"QQQM","SMH","SOXX","XBI","IBB","VNQ"}: macro += -10 if ten_y_change>.05 else (7 if ten_y_change<-.05 else 0)
    if primary in {"XLP","GLD","GDX"} and vix>22: macro+=10
    if primary in {"XLI","XLB","XLE","XLF"} and vix<22: macro+=5
    macro=clamp(macro)

    weights=(factor_weights or BASE_FACTOR_WEIGHTS).copy()
    total_w=sum(weights.values()) or 1
    weights={k:float(v)/total_w for k,v in weights.items()}
    factors={"momentum":mom,"relative_strength":rel,"volume":volume,"breadth":breadth,"macro":macro}
    base_total=sum(factors[k]*weights.get(k,0) for k in factors)

    spy_ext_chg=float(spy_snap.get("extended_change_pct") or 0.0); ext_relative=ext_session_chg-spy_ext_chg
    ext_adjustment=max(-4.0,min(4.0,ext_session_chg*.7+ext_relative*.8)) if session in {"premarket","afterhours"} else 0.0
    total=round_int(clamp(base_total+ext_adjustment)); base_score=round_int(clamp(base_total))

    ma20=float(close.tail(20).mean()) if len(close)>=20 else float(close.iloc[-1])
    dist_ma20=(float(close.iloc[-1])/ma20-1)*100 if ma20 else 0.0
    high20=float(close.tail(20).max()) if len(close)>=20 else float(close.max())
    dist_high20=(float(close.iloc[-1])/high20-1)*100 if high20 else 0.0
    prior_high=float(df["High"].iloc[-21:-1].max()) if "High" in df and len(df)>21 else high20
    today_high=float(df["High"].iloc[-1]) if "High" in df and len(df) else float(close.iloc[-1])
    breakout_failure=bool(prior_high and today_high>prior_high*1.001 and float(close.iloc[-1])<prior_high)
    overheat=bool(r5>5 and z5>1.7 and dist_ma20>4)

    gap_pct=0.0; gap_hold=None
    try:
        open_now=float(df["Open"].iloc[-1]); prev_close=float(df["Close"].iloc[-2])
        gap_pct=(open_now/prev_close-1)*100 if prev_close else 0.0
        gap_move=open_now-prev_close
        if abs(gap_pct)>=1 and gap_move:
            gap_hold=(float(close.iloc[-1])-prev_close)/gap_move
    except Exception: pass

    corr20=corr60=corr_change=None
    try:
        aligned=pd.concat([close.pct_change().rename("sector"), spy.pct_change().rename("spy")],axis=1).dropna()
        if len(aligned)>=20: corr20=float(aligned.tail(20).corr().iloc[0,1])
        if len(aligned)>=60: corr60=float(aligned.tail(60).corr().iloc[0,1])
        if corr20 is not None and corr60 is not None: corr_change=corr20-corr60
    except Exception: pass

    status="상승 사이클" if total>=75 else "초기 관심" if total>=60 else "중립" if total>=45 else "약화"
    action="추격주의 · 조정 대기" if status=="상승 사이클" and (r5>5 or overheat) else "분할 접근" if status=="상승 사이클" else "소액 진입 검토" if status=="초기 관심" and r5<3 else "관찰 · 조정 대기" if status=="초기 관심" else "추가매수 보류" if status=="약화" else "관찰"
    session_label={"premarket":"프리마켓","regular":"정규장","afterhours":"애프터마켓","closed":"장외 마지막"}.get(session,session)
    ext_text=f" · {session_label} {ext_session_chg:+.2f}% · SPY 대비 {ext_relative:+.2f}%p · 보정 {ext_adjustment:+.1f}점" if session in {"premarket","afterhours"} else ""
    reason=(f"{session_label} 최신가 확인 · 5일 {r5:+.1f}% · 20일 {r20:+.1f}% · SPY 대비 5일 {rs5:+.1f}%p / 20일 {rs20:+.1f}%p · "
            f"상대강도 변화 {rs_accel:+.1f}%p · 거래량 {vr:.2f}배 · 변동성조정 5일 z {z5:+.2f} · Breadth {breadth:.0f}점{ext_text}")
    try:
        daily_bar_date=str(pd.Timestamp(df.index[-1]).date())
    except Exception: daily_bar_date=""
    return {"status":status,"score":total,"base_score":base_score,"action":action,"reason":reason,
            "factors":{k:round_int(v) for k,v in factors.items()},
            "metrics":{"return_5d_pct":round(r5,2),"return_20d_pct":round(r20,2),"rs_vs_spy_5d_pctp":round(rs5,2),
                       "rs_vs_spy_20d_pctp":round(rs20,2),"rs_acceleration_pctp":round(rs_accel,2),"volume_expansion_ratio":round(float(vr),2),
                       "breadth_score":round(float(breadth),1),"session":session,"extended_change_pct":round(float(ext_session_chg),2),
                       "extended_vs_spy_pctp":round(float(ext_relative),2),"extended_score_adjustment":round(float(ext_adjustment),1),
                       "regular_base_score":base_score,"regular_close":round(float(sector_snap.get("regular_close") or close.iloc[-1]),6),
                       "latest_price":round(float(sector_snap.get("latest") or sector_snap.get("regular_close") or close.iloc[-1]),6),
                       "daily_bar_date":daily_bar_date,"history_points":int(len(close)),"daily_volatility_pct":round(daily_vol_pct,3),
                       "volatility_z5":round(z5,2),"volatility_z20":round(z20,2),"distance_ma20_pct":round(dist_ma20,2),
                       "distance_20d_high_pct":round(dist_high20,2),"overheat":overheat,"breakout_failure":breakout_failure,
                       "gap_pct":round(gap_pct,2),"gap_hold_ratio":round(float(gap_hold),2) if gap_hold is not None else None,
                       "corr20_spy":round(corr20,3) if corr20 is not None else None,"corr60_spy":round(corr60,3) if corr60 is not None else None,
                       "corr_change":round(corr_change,3) if corr_change is not None else None,"latest_quote_available":ext is not None,
                       "factor_weights":{k:round(v,4) for k,v in weights.items()}}}


def format_market(name, latest, prev, chg):
    if latest is None:
        return "-", "-", 0.0
    if name == "미 10년물":
        y = tnx_yield(latest)
        p = tnx_yield(prev) if prev is not None else y
        bp = (y - p) * 100
        return f"{y:.3f}%", f"{bp:+.1f}bp", bp
    if name in {"WTI 원유", "DXY"}:
        value = f"{latest:,.2f}"
    elif name == "달러/원":
        value = f"{latest:,.1f}"
    elif name == "비트코인":
        value = f"{latest:,.0f}"
    else:
        value = f"{latest:,.2f}"
    return value, f"{chg:+.2f}%", chg


def grade_score(grade):
    return {"우호": 80, "중립": 55, "주의": 35, "위험": 15}.get(grade, 55)


def investment_environment(macro):
    # weights reflect usefulness to broad risk assets; sum=100
    weights = {"고용": 20, "물가": 20, "금리": 20, "경기": 20, "유동성": 10, "위험선호": 10}
    weighted = sum(m.get("investment_score", 55) * weights.get(m["name"], 0) for m in macro)
    score = round_int(weighted / 100)
    if score >= 70:
        label = "🟢 우호"
        guidance = "위험자산에 비교적 우호적입니다. 강한 섹터를 중심으로 분할 접근하되 급등 추격은 구분하세요."
    elif score >= 55:
        label = "🔵 중립~우호"
        guidance = "선별 매수 환경입니다. 지수 전체보다 상대강도가 개선되는 섹터를 우선 관찰하세요."
    elif score >= 40:
        label = "🟡 중립~주의"
        guidance = "공격적 추격매수에는 불리합니다. 분할 접근과 현금 여력을 유지하고 금리민감 고밸류 자산은 선별하세요."
    else:
        label = "🔴 위험"
        guidance = "위험회피 환경입니다. 신규 고베타 비중 확대보다 방어와 현금 관리가 우선인 구간입니다."
    return {"score": score, "label": label, "guidance": guidance}


def interpolate_score(x, points):
    """Linear interpolation for a monotonic x-axis score map."""
    x = float(x)
    pts = sorted((float(px), float(py)) for px, py in points)
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y1
            t = (x - x0) / (x1 - x0)
            return y0 + (y1 - y0) * t
    return pts[-1][1]


def weighted_available(parts):
    """parts: [(value_or_none, weight), ...], renormalized when a source is missing."""
    usable = [(float(v), float(w)) for v, w in parts if v is not None and math.isfinite(float(v)) and w > 0]
    if not usable:
        return None
    total_w = sum(w for _, w in usable)
    return round_int(sum(v * w for v, w in usable) / total_w)


def rsi14(close):
    close = pd.Series(close).dropna().astype(float)
    if len(close) < 15:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    last_loss = float(loss.iloc[-1])
    last_gain = float(gain.iloc[-1])
    if not math.isfinite(last_gain) or not math.isfinite(last_loss):
        return None
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return 100 - (100 / (1 + rs))


def fetch_fear_greed():
    """Fetch CNN Fear & Greed robustly.

    CNN's graphdata endpoint is undocumented and can return anti-bot responses
    (notably 418/403/429) to cloud runners. Try browser-like, date-scoped URLs
    first, then the base URL. If CNN is still unavailable, fall back to a
    GitHub-hosted daily mirror that is rebuilt from the same CNN endpoint.
    """
    base = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    browser_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.cnn.com/markets/fear-and-greed",
        "Origin": "https://www.cnn.com",
        "Cache-Control": "no-cache",
    }

    def ko_rating(rating):
        rating = str(rating or "").strip().lower()
        return {
            "extreme fear": "극단적 공포",
            "fear": "공포",
            "neutral": "중립",
            "greed": "탐욕",
            "extreme greed": "극단적 탐욕",
        }.get(rating, rating or "확인")

    def as_float(v):
        try:
            x = float(v)
            return x if math.isfinite(x) else None
        except Exception:
            return None

    def parse_payload(payload, source_url):
        payload = payload or {}
        fg = payload.get("fear_and_greed") or {}
        hist = payload.get("fear_and_greed_historical") or {}
        score = as_float(fg.get("score"))
        rating = str(fg.get("rating") or "").strip().lower()
        timestamp = fg.get("timestamp")

        # Some responses may only expose the historical block.
        if score is None:
            score = as_float(hist.get("score"))
            rating = rating or str(hist.get("rating") or "").strip().lower()
            timestamp = timestamp or hist.get("timestamp")
        if score is None:
            data = hist.get("data") or []
            if data:
                latest = data[-1]
                score = as_float(latest.get("y"))
                rating = rating or str(latest.get("rating") or "").strip().lower()
                timestamp = timestamp or latest.get("x")
        if score is None:
            raise ValueError("CNN response missing Fear & Greed score")

        return {
            "available": True,
            "score": round(score, 1),
            "rating": rating,
            "rating_ko": ko_rating(rating),
            "previous_close": as_float(fg.get("previous_close")),
            "previous_1_week": as_float(fg.get("previous_1_week")),
            "previous_1_month": as_float(fg.get("previous_1_month")),
            "timestamp": timestamp,
            "source": "CNN",
            "source_url": source_url,
            "fallback": False,
        }

    errors = []
    today_et = datetime.now(ET).date()
    # Date-scoped endpoint is less likely to be rejected by CNN's anti-bot layer.
    candidates = [f"{base}/{(today_et - timedelta(days=i)).isoformat()}" for i in range(0, 4)]
    candidates.append(base)
    for endpoint in candidates:
        try:
            r = requests.get(endpoint, headers=browser_headers, timeout=20)
            if r.status_code in {403, 418, 429}:
                errors.append(f"{endpoint}: HTTP {r.status_code}")
                continue
            r.raise_for_status()
            result = parse_payload(r.json(), endpoint)
            print(f"fear greed ok: CNN {result['score']} ({result['rating']})")
            return result
        except Exception as e:
            errors.append(f"{endpoint}: {e}")

    # Fallback: public GitHub mirror, rebuilt from CNN data after US market close.
    # This can be one trading day behind intraday, but prevents a blank card.
    mirror = "https://raw.githubusercontent.com/whit3rabbit/fear-greed-data/main/fear-greed.csv"
    try:
        from io import StringIO
        r = requests.get(mirror, headers=browser_headers, timeout=20)
        r.raise_for_status()
        df = pd.read_csv(StringIO(r.text))
        if df.empty:
            raise ValueError("empty mirror CSV")
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df["Fear Greed"] = pd.to_numeric(df["Fear Greed"], errors="coerce")
        df = df.dropna(subset=["Date", "Fear Greed"]).sort_values("Date")
        if df.empty:
            raise ValueError("mirror CSV has no valid rows")
        latest = df.iloc[-1]
        latest_date = latest["Date"].date()
        score = float(latest["Fear Greed"])
        rating = str(latest.get("Rating", "") or "").strip().lower()

        week_target = pd.Timestamp(latest_date - timedelta(days=7))
        prior_rows = df[df["Date"] <= week_target]
        prev_week = float(prior_rows.iloc[-1]["Fear Greed"]) if not prior_rows.empty else None

        print(f"fear greed fallback: mirror {score:.1f} ({rating}) as of {latest_date}")
        return {
            "available": True,
            "score": round(score, 1),
            "rating": rating,
            "rating_ko": ko_rating(rating),
            "previous_close": None,
            "previous_1_week": round(prev_week, 1) if prev_week is not None else None,
            "previous_1_month": None,
            "timestamp": latest_date.isoformat(),
            "source": "CNN 데이터 GitHub 미러",
            "source_url": mirror,
            "fallback": True,
        }
    except Exception as e:
        errors.append(f"mirror: {e}")

    err = " | ".join(errors[-4:])
    print(f"fear greed warning: {err}")
    return {
        "available": False,
        "score": None,
        "rating": "unavailable",
        "rating_ko": "수집 실패",
        "error": err,
        "source": "unavailable",
        "fallback": False,
    }


def timing_label(score, kind):
    if score is None:
        return "확인 중"
    score = float(score)
    if kind == "low_buy":
        return "매력 높음" if score >= 75 else "관심" if score >= 60 else "보통" if score >= 45 else "낮음"
    return "확인" if score >= 70 else "개선" if score >= 55 else "대기" if score >= 40 else "미확인"


def market_timing_signals(spy_df, spy_ext_price, vix_latest, env_score):
    close = spy_df["Close"].dropna().astype(float).copy()
    vol = spy_df["Volume"].fillna(0).astype(float).copy()
    if spy_ext_price is not None and len(close):
        close.iloc[-1] = float(spy_ext_price)

    current = float(close.iloc[-1])
    high_1y = float(close.tail(min(252, len(close))).max())
    drawdown_pct = max(0.0, (1 - current / high_1y) * 100) if high_1y else 0.0
    rsi = rsi14(close)
    r5 = (current / float(close.iloc[-6]) - 1) * 100 if len(close) > 6 else 0.0
    ma20 = float(close.tail(20).mean()) if len(close) >= 20 else current
    vs_ma20 = (current / ma20 - 1) * 100 if ma20 else 0.0
    v5 = float(vol.tail(5).mean()) if len(vol) else 0.0
    v20 = float(vol.tail(20).mean()) if len(vol) >= 20 else max(v5, 1.0)
    volume_ratio = v5 / v20 if v20 else 1.0

    # Equal-weight S&P 500 ETF is used only as a broad participation proxy.
    rsp_rs20 = None
    rsp_rs5 = None
    try:
        rsp = daily_history("RSP", "6mo")["Close"].dropna().astype(float).copy()
        rsp_ext, _, _ = latest_extended("RSP")
        if rsp_ext is not None and len(rsp):
            rsp.iloc[-1] = float(rsp_ext)
        if len(rsp) > 21 and len(close) > 21:
            rsp20 = (float(rsp.iloc[-1]) / float(rsp.iloc[-21]) - 1) * 100
            spy20 = (current / float(close.iloc[-21]) - 1) * 100
            rsp_rs20 = rsp20 - spy20
        if len(rsp) > 6 and len(close) > 6:
            rsp5 = (float(rsp.iloc[-1]) / float(rsp.iloc[-6]) - 1) * 100
            spy5 = (current / float(close.iloc[-6]) - 1) * 100
            rsp_rs5 = rsp5 - spy5
    except Exception as e:
        print(f"RSP breadth proxy warning: {e}")

    vix_5d_change = None
    try:
        vh = daily_history("^VIX", "3mo")["Close"].dropna().astype(float)
        if len(vh) > 6:
            vix_5d_change = (float(vh.iloc[-1]) / float(vh.iloc[-6]) - 1) * 100
    except Exception as e:
        print(f"VIX history warning: {e}")

    fg = fetch_fear_greed()
    fg_inverse = 100 - float(fg["score"]) if fg.get("score") is not None else None

    drawdown_component = interpolate_score(drawdown_pct, [(0, 12), (3, 25), (5, 38), (10, 58), (20, 82), (30, 96), (40, 100)])
    rsi_component = interpolate_score(rsi, [(20, 100), (30, 85), (40, 65), (50, 45), (60, 25), (70, 10), (80, 3)]) if rsi is not None else None
    vix_component = interpolate_score(vix_latest, [(12, 5), (15, 15), (20, 35), (25, 55), (30, 72), (40, 90), (50, 100)]) if vix_latest is not None else None
    breadth_stress = max(0.0, -rsp_rs20) if rsp_rs20 is not None else None
    breadth_stress_component = interpolate_score(breadth_stress, [(0, 30), (1, 40), (3, 60), (5, 76), (8, 94), (12, 100)]) if breadth_stress is not None else None

    low_score = weighted_available([
        (fg_inverse, 30),
        (drawdown_component, 25),
        (rsi_component, 15),
        (vix_component, 15),
        (breadth_stress_component, 15),
    ])

    momentum_component = interpolate_score(r5, [(-8, 5), (-5, 15), (-2, 30), (0, 48), (2, 65), (5, 82), (8, 95)])
    ma_component = interpolate_score(vs_ma20, [(-10, 5), (-5, 20), (-2, 38), (0, 55), (3, 72), (7, 90), (12, 98)])
    vix_reversal_component = interpolate_score(vix_5d_change, [(-45, 98), (-25, 82), (-10, 65), (0, 50), (10, 35), (25, 18), (50, 5)]) if vix_5d_change is not None else None
    breadth_confirm_component = interpolate_score(rsp_rs5, [(-5, 10), (-2, 30), (0, 50), (2, 68), (5, 88), (8, 98)]) if rsp_rs5 is not None else None
    volume_confirm_component = clamp(50 + (volume_ratio - 1) * 80 + (10 if r5 > 0 else -10))

    reversal_score = weighted_available([
        (momentum_component, 25),
        (ma_component, 25),
        (vix_reversal_component, 20),
        (breadth_confirm_component, 15),
        (volume_confirm_component, 15),
    ])

    if low_score is None or reversal_score is None:
        summary = "일부 시장 타이밍 데이터가 아직 수집되지 않았습니다."
        action = "데이터 확인"
    elif low_score >= 75 and reversal_score >= 60:
        summary = "공포·낙폭에 따른 가격 매력과 반전 신호가 함께 개선되고 있습니다."
        action = "1차 분할매수 후보"
    elif low_score >= 75 and reversal_score < 45:
        summary = "가격 매력은 높지만 반전 확인이 부족합니다. 떨어지는 칼 위험을 함께 보세요."
        action = "관찰 · 소액 분할만"
    elif low_score >= 60 and reversal_score >= 55:
        summary = "저점 매력과 반전 신호가 동시에 살아나는 구간입니다."
        action = "분할 접근 검토"
    elif low_score < 45 and reversal_score >= 65 and env_score >= 55:
        summary = "추세는 확인되고 있지만 저점매수 매력은 낮습니다. 강한 시장의 추격 위험을 구분하세요."
        action = "추격보다 조정 대기"
    elif low_score < 45:
        summary = "공포·낙폭 기준 저점 매력은 크지 않습니다."
        action = "선별 관찰"
    else:
        summary = "가격 매력과 반전 신호가 엇갈리는 중간 구간입니다."
        action = "신호 확인 대기"

    fg_note = "CNN 시장심리 원값"
    if fg.get("score") is not None:
        if fg.get("fallback"):
            fg_note = f"CNN 미러 · {fg.get('timestamp','-')} 마감 기준 · 낮을수록 공포"
        else:
            fg_note = f"CNN 직접 수집 · 1주 전 {fg.get('previous_1_week') if fg.get('previous_1_week') is not None else '-'} · 낮을수록 공포"
    else:
        fg_note = "CNN 데이터 수집 실패 · 저점점수에서 자동 제외"

    return {
        "low_buy": {
            "score": low_score,
            "label": timing_label(low_score, "low_buy"),
            "note": f"SPY 고점 대비 -{drawdown_pct:.1f}% · RSI {rsi:.1f}" if rsi is not None else f"SPY 고점 대비 -{drawdown_pct:.1f}%",
            "components": {
                "fear_greed_inverse": round(fg_inverse, 1) if fg_inverse is not None else None,
                "drawdown": round(drawdown_component, 1),
                "rsi": round(rsi_component, 1) if rsi_component is not None else None,
                "vix": round(vix_component, 1) if vix_component is not None else None,
                "breadth_stress": round(breadth_stress_component, 1) if breadth_stress_component is not None else None,
            },
        },
        "reversal": {
            "score": reversal_score,
            "label": timing_label(reversal_score, "reversal"),
            "note": f"SPY 5일 {r5:+.1f}% · 20일선 대비 {vs_ma20:+.1f}%",
            "components": {
                "momentum_5d": round(momentum_component, 1),
                "ma20_recovery": round(ma_component, 1),
                "vix_stabilization": round(vix_reversal_component, 1) if vix_reversal_component is not None else None,
                "breadth_confirmation": round(breadth_confirm_component, 1) if breadth_confirm_component is not None else None,
                "volume_confirmation": round(volume_confirm_component, 1),
            },
        },
        "fear_greed": {**fg, "label": fg.get("rating_ko", "확인"), "note": fg_note},
        "metrics": {
            "spy_drawdown_pct": round(drawdown_pct, 2),
            "spy_rsi14": round(rsi, 2) if rsi is not None else None,
            "spy_5d_pct": round(r5, 2),
            "spy_vs_ma20_pct": round(vs_ma20, 2),
            "vix": round(float(vix_latest), 2) if vix_latest is not None else None,
            "vix_5d_pct": round(vix_5d_change, 2) if vix_5d_change is not None else None,
            "rsp_vs_spy_20d_pctp": round(rsp_rs20, 2) if rsp_rs20 is not None else None,
            "rsp_vs_spy_5d_pctp": round(rsp_rs5, 2) if rsp_rs5 is not None else None,
        },
        "summary": summary,
        "action": action,
        "sources": [
            {"label":"CNN Fear & Greed","note":"시장 심리 원문 · 7개 심리 지표 종합","url":"https://www.cnn.com/markets/fear-and-greed"},
            {"label":"Cboe VIX","note":"S&P 500 옵션 기반 30일 예상 변동성","url":"https://www.cboe.com/tradable_products/vix/"},
            {"label":"Yahoo Finance · SPY","note":"낙폭·RSI·모멘텀 계산용 시세","url":yahoo_url("SPY")},
            {"label":"Yahoo Finance · RSP","note":"시장 폭 참여도 대용치(동일가중 S&P500)","url":yahoo_url("RSP")},
        ],
    }


# ---------- official macro calendar ----------

def unfold_ics(text):
    lines = text.replace("\r\n", "\n").split("\n")
    out = []
    for line in lines:
        if line.startswith((" ", "\t")) and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def parse_ics_datetime(value):
    value = value.strip()
    if value.endswith("Z"):
        dt = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        return dt.astimezone(ET)
    fmt = "%Y%m%dT%H%M%S" if len(value) >= 15 else "%Y%m%dT%H%M"
    return datetime.strptime(value[:15] if len(value) >= 15 else value, fmt).replace(tzinfo=ET)


def fetch_bls_events(now_et):
    url = "https://www.bls.gov/schedule/news_release/bls.ics"
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    events = []
    block = None
    for line in unfold_ics(r.text):
        if line == "BEGIN:VEVENT":
            block = {}
        elif line == "END:VEVENT" and block is not None:
            summary = block.get("SUMMARY", "")
            dt = block.get("DTSTART")
            if dt and dt >= now_et - timedelta(hours=2):
                if "Consumer Price Index" in summary:
                    events.append(make_event("CPI (근원 CPI)", dt, 3, "BLS", "https://www.bls.gov/schedule/news_release/cpi.htm", summary))
                elif "Producer Price Index" in summary:
                    events.append(make_event("PPI (최종수요)", dt, 2, "BLS", "https://www.bls.gov/schedule/news_release/ppi.htm", summary))
                elif "Employment Situation" in summary:
                    events.append(make_event("NFP / 실업률", dt, 3, "BLS", "https://www.bls.gov/schedule/news_release/empsit.htm", summary))
            block = None
        elif block is not None:
            if line.startswith("SUMMARY:"):
                block["SUMMARY"] = line.split(":", 1)[1].replace("\\,", ",")
            elif line.startswith("DTSTART"):
                raw = line.split(":", 1)[1]
                try:
                    block["DTSTART"] = parse_ics_datetime(raw)
                except Exception:
                    pass
    return events


def parse_date_text(text):
    for fmt in ("%B %d, %Y", "%b. %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except Exception:
            continue
    return None


def fetch_retail_event(now_et):
    url = "https://www.census.gov/retail/release_schedule.html"
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    heading = None
    for h in soup.find_all(["h2", "h3", "h4"]):
        if "Advance Monthly Retail Trade Report" in h.get_text(" ", strip=True):
            heading = h
            break
    if not heading:
        return None
    table = heading.find_next("table")
    if not table:
        return None
    candidates = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        date = parse_date_text(cells[1])
        if date:
            dt = datetime(date.year, date.month, date.day, 8, 30, tzinfo=ET)
            if dt >= now_et - timedelta(hours=2):
                candidates.append(dt)
    if not candidates:
        return None
    dt = min(candidates)
    return make_event("소매판매 (근원)", dt, 3, "U.S. Census Bureau", url, "Advance Monthly Retail Trade Report")


def fetch_fomc_event(now_et):
    url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    dates = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        text = BeautifulSoup(r.text, "html.parser").get_text("\n", strip=True)
        year = now_et.year
        # Limit parsing to current year's FOMC section when possible.
        start = text.find(f"{year} FOMC Meetings")
        section = text[start:start + 7000] if start >= 0 else text
        month_map = {m: i for i, m in enumerate([
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December"], 1)}
        pattern = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:-(\d{1,2}))?\*?")
        for m in pattern.finditer(section):
            month = month_map[m.group(1)]
            day = int(m.group(3) or m.group(2))  # statement is normally on second day
            try:
                dt = datetime(year, month, day, 14, 0, tzinfo=ET)
                if dt >= now_et - timedelta(hours=3):
                    dates.append(dt)
            except ValueError:
                pass
    except Exception:
        pass

    # official-calendar fallback for 2026/2027 if the page layout changes
    fallback = {
        2026: [(1,28),(3,18),(4,29),(6,17),(7,29),(9,16),(10,28),(12,9)],
        2027: [(1,27),(3,17),(4,28),(6,9),(7,28),(9,15),(10,27),(12,8)],
    }
    for month, day in fallback.get(now_et.year, []):
        dt = datetime(now_et.year, month, day, 14, 0, tzinfo=ET)
        if dt >= now_et - timedelta(hours=3):
            dates.append(dt)
    if not dates:
        return None
    dt = min(dates)
    return make_event("FOMC 회의 (금리결정)", dt, 3, "Federal Reserve", url, "FOMC statement / press conference")


def next_initial_claims(now_et):
    # Weekly claims are normally Thursday 08:30 ET. Holiday exceptions can occur.
    days = (3 - now_et.weekday()) % 7
    date = now_et.date() + timedelta(days=days)
    dt = datetime(date.year, date.month, date.day, 8, 30, tzinfo=ET)
    if dt < now_et:
        dt += timedelta(days=7)
    return make_event("신규 실업수당", dt, 1, "U.S. Department of Labor", "https://www.dol.gov/ui/data.pdf", "통상 목요일 08:30 ET · 공휴일 예외 가능")


def make_event(name, dt_et, impact, source, source_url, note=""):
    dt_kst = dt_et.astimezone(KST)
    return {
        "name": name,
        "date_kst": dt_kst.strftime("%m/%d (%a)").replace("Mon","월").replace("Tue","화").replace("Wed","수").replace("Thu","목").replace("Fri","금").replace("Sat","토").replace("Sun","일"),
        "time_kst": dt_kst.strftime("%H:%M"),
        "date_et": dt_et.strftime("%Y-%m-%d"),
        "time_et": dt_et.strftime("%H:%M"),
        "iso_kst": dt_kst.isoformat(timespec="minutes"),
        "impact": impact,
        "source": source,
        "source_url": source_url,
        "note": note,
    }


def official_calendar(now_et):
    events = []
    try:
        events.extend(fetch_bls_events(now_et))
    except Exception as e:
        print("BLS calendar warning:", e)
    try:
        retail = fetch_retail_event(now_et)
        if retail:
            events.append(retail)
    except Exception as e:
        print("Retail calendar warning:", e)
    try:
        fomc = fetch_fomc_event(now_et)
        if fomc:
            events.append(fomc)
    except Exception as e:
        print("FOMC calendar warning:", e)
    events.append(next_initial_claims(now_et))
    # Deduplicate same label/time and keep next 6.
    uniq = {}
    for e in events:
        uniq[(e["name"], e["iso_kst"])] = e
    return sorted(uniq.values(), key=lambda e: e["iso_kst"])[:6]


# ---------- history, stabilization, validation ----------

def load_history():
    if not HISTORY.exists(): return {"days": {}, "signals": [], "alert_state": {}}
    try:
        data=json.loads(HISTORY.read_text(encoding="utf-8")); data.setdefault("days",{}); data.setdefault("signals",[]); data.setdefault("alert_state",{}); return data
    except Exception: return {"days": {}, "signals": [], "alert_state": {}}


def previous_sector_records(history, today, name, limit=10):
    days=history.get("days",{}); dates=sorted([d for d in days if d<today])[-limit:]
    out=[]
    for d in dates:
        for s in days[d].get("sectors",[]):
            if s.get("name")==name:
                out.append({"date":d,**s}); break
    return out


def hysteresis_status(score, prev_status=None):
    s=float(score)
    if prev_status=="상승 사이클":
        return "상승 사이클" if s>=72 else "초기 관심" if s>=58 else "중립" if s>=43 else "약화"
    if prev_status=="초기 관심":
        return "상승 사이클" if s>=76 else "초기 관심" if s>=57 else "중립" if s>=43 else "약화"
    if prev_status=="중립":
        return "상승 사이클" if s>=76 else "초기 관심" if s>=62 else "중립" if s>=43 else "약화"
    if prev_status=="약화":
        return "상승 사이클" if s>=76 else "초기 관심" if s>=62 else "중립" if s>=46 else "약화"
    return "상승 사이클" if s>=75 else "초기 관심" if s>=60 else "중립" if s>=45 else "약화"


def trend_label(delta3):
    if delta3>=8: return "↑ 빠른 개선"
    if delta3>=3: return "↗ 완만한 개선"
    if delta3<=-8: return "↓ 급격한 약화"
    if delta3<=-3: return "↘ 약화"
    return "→ 유지"


def data_quality_profile(metrics, now_et):
    score=100; flags=[]
    if int(metrics.get("history_points") or 0)<60: score-=25; flags.append("히스토리 부족")
    vr=metrics.get("volume_expansion_ratio")
    if vr is None or not math.isfinite(float(vr)) or float(vr)<=0: score-=20; flags.append("거래량 데이터 확인")
    bar=metrics.get("daily_bar_date")
    if bar:
        try:
            stale=(now_et.date()-datetime.strptime(bar,"%Y-%m-%d").date()).days
            if stale>4: score-=25; flags.append(f"일봉 {stale}일 경과")
        except Exception: score-=10
    else: score-=15; flags.append("일봉 시점 확인 불가")
    if metrics.get("session") in {"premarket","regular","afterhours"} and not metrics.get("latest_quote_available"):
        score-=20; flags.append("장중/장외 최신가 누락")
    if abs(float(metrics.get("extended_change_pct") or 0))>15: score-=15; flags.append("장외 이상치 가능")
    score=round_int(clamp(score)); grade="A" if score>=85 else "B" if score>=70 else "C" if score>=50 else "D"
    return {"score":score,"grade":grade,"flags":flags}


def determine_action(status, trend, metrics, regime_label):
    overheat=bool(metrics.get("overheat")); fake=bool(metrics.get("breakout_failure"))
    if status=="약화": action="추가매수 보류"
    elif status=="중립": action="관찰"
    elif overheat or fake: action="추격주의 · 조정 대기"
    elif trend in {"↓ 급격한 약화","↘ 약화"}: action="관찰 · 조정 대기"
    elif status=="상승 사이클": action="분할 접근"
    elif status=="초기 관심" and trend in {"↑ 빠른 개선","↗ 완만한 개선"}: action="소액 진입 검토"
    elif status=="초기 관심" and float(metrics.get("return_5d_pct") or 0)<3: action="소액 진입 검토"
    else: action="관찰 · 조정 대기"
    if regime_label=="Risk-off":
        if action=="분할 접근": action="소액 진입 검토"
        elif action=="소액 진입 검토": action="관찰 · 조정 대기"
    return action


def signal_durations(history, today, name, action):
    """Track the broader buy-signal streak separately from the exact action stage.

    Example: 소액 진입 검토 3일 -> 분할 접근 오늘
      buy_signal_days = 4
      stage_days = 1
    """
    if not action:
        return {"buy_signal_days": 0, "stage_days": 0}

    records = previous_sector_records(history, today, name, 30)

    stage_days = 1
    for r in reversed(records):
        if r.get("action") == action:
            stage_days += 1
        else:
            break

    buy_targets = {"소액 진입 검토", "분할 접근"}
    buy_signal_days = 0
    if action in buy_targets:
        buy_signal_days = 1
        for r in reversed(records):
            if r.get("action") in buy_targets:
                buy_signal_days += 1
            else:
                break

    return {"buy_signal_days": buy_signal_days, "stage_days": stage_days}


def signal_story(history, today, name, action, buy_signal_days, stage_days):
    """Human-friendly description of how the current action evolved."""
    records = previous_sector_records(history, today, name, 5)
    prev_action = records[-1].get("action") if records else None
    buy_targets = {"소액 진입 검토", "분할 접근"}

    if action in buy_targets:
        if int(buy_signal_days or 0) <= 1:
            if action == "소액 진입 검토":
                return "🟡 오늘 신규 관심"
            return "🟢 오늘 분할 접근 진입"

        if prev_action in buy_targets and prev_action != action:
            if prev_action == "소액 진입 검토" and action == "분할 접근":
                return f"🟢 관심 {buy_signal_days}일째 · 오늘 분할 접근으로 상향 ↑"
            if prev_action == "분할 접근" and action == "소액 진입 검토":
                return f"🟡 관심 흐름 {buy_signal_days}일째 · 오늘 소액 진입으로 하향 ↓"

        if action == "소액 진입 검토":
            return f"🟡 관심 신호 {buy_signal_days}일째"
        return f"🟢 분할 접근 {stage_days}일째 · 관심 흐름 {buy_signal_days}일 지속"

    if prev_action in buy_targets:
        return f"⚪ 오늘 매수 신호 종료 · {action} 전환"

    if int(stage_days or 0) > 1:
        return f"현재 판단 {stage_days}일째 유지"
    return "오늘 판단 변경"


def enrich_sector_signal(s, history, today, now_et, regime, event_risk, participation):
    prev=previous_sector_records(history,today,s["name"],10)
    prev_status=prev[-1].get("status") if prev else None
    historical_raw=[float(x.get("raw_score",x.get("score",50))) for x in prev[-4:]]
    raw=float(s.get("score",50)); series=historical_raw+[raw]
    avg3=sum(series[-3:])/len(series[-3:]); avg5=sum(series[-5:])/len(series[-5:])
    smoothed=round_int(clamp(raw*.60+avg3*.40))
    delta3=round(raw-series[-3],1) if len(series)>=3 else round(raw-series[0],1)
    peak=max(series[-5:]); peak_drop=round(raw-peak,1)
    tr_label=trend_label(delta3)
    status=hysteresis_status(smoothed,prev_status)
    # Avoid excessive lag: a sharp raw-score improvement can enter early-interest
    # only when the sector-participation proxy also confirms the move.
    if status=="중립" and raw>=60 and tr_label=="↑ 빠른 개선" and float(participation.get("score",50))>=60:
        status="초기 관심"

    metrics=s.get("metrics") or {}; quality=data_quality_profile(metrics,now_et)
    action=determine_action(status,tr_label,metrics,regime.get("label"))
    durations=signal_durations(history,today,s["name"],action)
    buy_signal_days=int(durations.get("buy_signal_days") or 0)
    stage_days=int(durations.get("stage_days") or 0)
    # For confidence persistence, keep continuity across a strengthening buy-stage
    # transition (소액 진입 검토 -> 분할 접근) instead of resetting to day 1.
    persistence_days=buy_signal_days if buy_signal_days>0 else stage_days
    story=signal_story(history,today,s["name"],action,buy_signal_days,stage_days)

    base_raw=float(metrics.get("regular_base_score") or raw)
    base_series=historical_raw+[base_raw]; base_avg3=sum(base_series[-3:])/len(base_series[-3:]); base_smoothed=round_int(clamp(base_raw*.60+base_avg3*.40))
    base_status=hysteresis_status(base_smoothed,prev_status); base_action=determine_action(base_status,tr_label,{**metrics,"overheat":False,"breakout_failure":False},regime.get("label"))
    provisional=bool(metrics.get("session") in {"premarket","afterhours"} and (status!=base_status or action!=base_action))

    factors=s.get("factors") or {}; factor_values=[float(v) for v in factors.values() if v is not None]
    if status in {"상승 사이클","초기 관심"}: coherence=sum(1 for v in factor_values if v>=55)/max(1,len(factor_values))*100
    elif status=="약화": coherence=sum(1 for v in factor_values if v<=45)/max(1,len(factor_values))*100
    else: coherence=70-max(0,(max(factor_values)-min(factor_values)) if factor_values else 0)*.5
    persistence=clamp(45+min(persistence_days,5)*8+(8 if tr_label in {"↑ 빠른 개선","↗ 완만한 개선"} and status in {"상승 사이클","초기 관심"} else 0))
    regime_fit=90 if regime.get("label")=="Risk-on" and status in {"상승 사이클","초기 관심"} else 35 if regime.get("label")=="Risk-off" and status in {"상승 사이클","초기 관심"} else 70
    confidence=quality["score"]*.30+float(participation.get("score",50))*.25+clamp(coherence)*.20+persistence*.15+regime_fit*.10
    # A sector becoming less correlated with SPY while its relative strength is positive
    # is treated as a small confirmation that the move is sector-specific rather than pure beta.
    corr_change=metrics.get("corr_change"); rs5=float(metrics.get("rs_vs_spy_5d_pctp") or 0)
    if corr_change is not None and float(corr_change)<=-.20 and rs5>0:
        confidence+=4
    penalties=0; flags=[]
    if provisional: penalties+=10; flags.append("장외 잠정 신호")
    if metrics.get("breakout_failure"): penalties+=12; flags.append("돌파 실패 가능성")
    if metrics.get("overheat"): penalties+=8; flags.append("단기 과열")
    if abs(float(metrics.get("gap_pct") or 0))>=1 and metrics.get("gap_hold_ratio") is not None and float(metrics.get("gap_hold_ratio"))<.35:
        penalties+=6; flags.append("갭 메움 진행")
    if event_risk.get("active"): penalties+=event_risk.get("penalty",0); flags.append("주요 이벤트 임박")
    flags.extend(quality.get("flags") or [])
    conf=round_int(clamp(confidence-penalties)); grade="A" if conf>=80 else "B" if conf>=65 else "C" if conf>=50 else "D"

    metrics["sector_participation_score"]=participation.get("score"); metrics["sector_participation_positive_pct"]=participation.get("positive_pct")
    metrics["sector_participation_outperform_pct"]=participation.get("outperform_pct"); metrics["provisional"]=provisional
    metrics["event_risk"]=event_risk.get("label") if event_risk.get("active") else None
    return {**s,"raw_score":round_int(raw),"score":smoothed,"status":status,"action":action,"metrics":metrics,
            "trend":{"avg3":round(avg3,1),"avg5":round(avg5,1),"delta_3d":delta3,"peak_drop_5d":peak_drop,"label":tr_label,"series":[round(x,1) for x in series[-5:]]},
            "signal_days":(buy_signal_days if buy_signal_days>0 else stage_days),
            "buy_signal_days":buy_signal_days,"stage_days":stage_days,"signal_story":story,
            "confidence":{"score":conf,"grade":grade},"data_quality":quality,"participation":participation,
            "provisional":provisional,"flags":list(dict.fromkeys(flags))[:6]}


def assign_sector_ranks(sectors, history, today):
    ordered=sorted(sectors,key=lambda x:(-float(x.get("score",0)),x.get("name","")))
    current={s["name"]:i+1 for i,s in enumerate(ordered)}
    days=history.get("days",{}); prev_dates=sorted([d for d in days if d<today]); previous={}
    if prev_dates:
        rows=days[prev_dates[-1]].get("sectors",[]); prow=sorted(rows,key=lambda x:(-float(x.get("score",0)),x.get("name","")))
        previous={s.get("name"):i+1 for i,s in enumerate(prow)}
    for s in sectors:
        s["rank"]=current.get(s["name"]); pr=previous.get(s["name"]); s["rank_change"]=(pr-s["rank"]) if pr else None
    return sectors


def change_rank(status): return {"약화":0,"중립":1,"초기 관심":2,"상승 사이클":3}.get(status,1)


def compute_changes(sectors, history, today):
    days=history.get("days",{}); previous_dates=sorted([d for d in days if d<today])
    if not previous_dates: return {"improved":[],"worsened":[],"unchanged":[s["etfs"][0] for s in sectors],"note":"전일 비교 데이터 적재 중 · 다음 거래일부터 상태 변화를 표시합니다."}
    prev_date=previous_dates[-1]; prev={x["name"]:x for x in days[prev_date].get("sectors",[])}
    improved=[]; worsened=[]; unchanged=[]
    for s in sectors:
        p=prev.get(s["name"])
        if not p: unchanged.append(s["etfs"][0]); continue
        delta=int(s["score"])-int(p.get("score",s["score"])); rdelta=change_rank(s["status"])-change_rank(p.get("status","중립"))
        obj={"name":s["name"],"symbol":s["etfs"][0],"from_status":p.get("status","-"),"to_status":s["status"],"delta":delta,
             "trend":(s.get("trend") or {}).get("label"),"rank":s.get("rank"),"rank_change":s.get("rank_change")}
        if rdelta>0 or (rdelta==0 and delta>=5): improved.append(obj)
        elif rdelta<0 or (rdelta==0 and delta<=-5): worsened.append(obj)
        else: unchanged.append(s["etfs"][0])
    improved.sort(key=lambda x:x["delta"],reverse=True); worsened.sort(key=lambda x:x["delta"])
    return {"improved":improved,"worsened":worsened,"unchanged":unchanged,"note":f"비교 기준: {prev_date} 최종 저장값"}


def evaluate_signal_outcomes(history):
    signals=history.setdefault("signals",[]); by_symbol={}
    for e in signals:
        if all((e.get("results") or {}).get(f"return_{h}d_pct") is not None for h in (5,10,20)): continue
        sym=e.get("symbol")
        if not sym: continue
        if sym not in by_symbol:
            try: by_symbol[sym]=daily_history(sym,"2y")
            except Exception as ex: print(f"signal outcome {sym} warning: {ex}"); by_symbol[sym]=None
        df=by_symbol[sym]
        if df is None or df.empty: continue
        dates=[str(pd.Timestamp(x).date()) for x in df.index]; target=e.get("entry_bar_date") or e.get("date")
        candidates=[i for i,d in enumerate(dates) if d<=target]
        if not candidates: continue
        idx=candidates[-1]; entry=float(e.get("entry_price") or df["Close"].iloc[idx]); results=e.setdefault("results",{})
        for h in (5,10,20):
            if idx+h<len(df): results[f"return_{h}d_pct"]=round((float(df["Close"].iloc[idx+h])/entry-1)*100,2)
            else: results.setdefault(f"return_{h}d_pct",None)
        if idx+20<len(df):
            future=df["Close"].iloc[idx+1:idx+21].astype(float); results["max_drawdown_20d_pct"]=round(min(0.0,(float(future.min())/entry-1)*100),2)
        else: results.setdefault("max_drawdown_20d_pct",None)
    history["signals"]=[e for e in signals if e.get("date","") >= (datetime.now(KST)-timedelta(days=400)).strftime("%Y-%m-%d")]


def walk_forward_calibration(history):
    matured=[e for e in history.get("signals",[]) if (e.get("results") or {}).get("return_20d_pct") is not None and e.get("factors")]
    weights=BASE_FACTOR_WEIGHTS.copy(); correlations={}; n=len(matured); active=False
    if n>=20:
        rows=[]
        for e in matured:
            row={k:(e.get("factors") or {}).get(k) for k in BASE_FACTOR_WEIGHTS}; row["ret"]=(e.get("results") or {}).get("return_20d_pct"); rows.append(row)
        df=pd.DataFrame(rows).apply(pd.to_numeric,errors="coerce")
        for k in BASE_FACTOR_WEIGHTS:
            c=df[[k,"ret"]].dropna().corr().iloc[0,1] if len(df[[k,"ret"]].dropna())>=10 else 0
            correlations[k]=round(float(c) if pd.notna(c) else 0.0,3)
    if n>=60:
        active=True; tilted={}
        for k,b in BASE_FACTOR_WEIGHTS.items():
            c=max(-.25,min(.25,float(correlations.get(k,0))))
            tilted[k]=b*(1+c*.4)
        total=sum(tilted.values()); weights={k:v/total for k,v in tilted.items()}
    return {"active":active,"sample_20d":n,"minimum_sample":60,"weights":weights,"correlations":correlations,
            "note":"20거래일 결과 60건 전까지 기본 가중치 유지. 이후 상관 기반 소폭 보정(과최적화 방지 제한)."}


def record_signal_entries(history, sectors, today, regime):
    signals=history.setdefault("signals",[]); ids={e.get("id") for e in signals}; targets={"소액 진입 검토","분할 접근"}
    for s in sectors:
        prev=previous_sector_records(history,today,s["name"],1); prev_action=prev[-1].get("action") if prev else None
        action=s.get("action")
        # First run establishes a baseline; do not back-fill current signals as if they newly occurred.
        if not prev or prev_action is None: continue
        if action not in targets or action==prev_action: continue
        m=s.get("metrics") or {}; eid=f"{today}|{s['name']}|{action}"
        if eid in ids: continue
        signals.append({"id":eid,"date":today,"entry_bar_date":today,"name":s["name"],"symbol":s["etfs"][0],
                        "action":action,"entry_price":m.get("latest_price") or m.get("regular_close"),"entry_score":s.get("score"),"entry_raw_score":s.get("raw_score"),
                        "entry_confidence":(s.get("confidence") or {}).get("score"),"regime":regime.get("label"),"factors":s.get("factors") or {},"results":{}})
        ids.add(eid)


def signal_scorecard(history, calibration):
    sig=history.get("signals",[]); horizons={}
    for h in (5,10,20):
        vals=[float((e.get("results") or {}).get(f"return_{h}d_pct")) for e in sig if (e.get("results") or {}).get(f"return_{h}d_pct") is not None]
        horizons[str(h)]={"count":len(vals),"win_rate":round(sum(v>0 for v in vals)/len(vals)*100,1) if vals else None,"avg_return_pct":round(sum(vals)/len(vals),2) if vals else None}
    mdd=[float((e.get("results") or {}).get("max_drawdown_20d_pct")) for e in sig if (e.get("results") or {}).get("max_drawdown_20d_pct") is not None]
    return {"total_signals":len(sig),"horizons":horizons,"avg_max_drawdown_20d_pct":round(sum(mdd)/len(mdd),2) if mdd else None,
            "calibration":calibration,"note":"신호 발생 당시 정규장 기준가를 기록해 이후 5·10·20거래일 성과를 자동 추적합니다."}


def save_history(history, today, sectors, regime=None):
    days=history.setdefault("days",{})
    days[today]={"regime":regime or {},"sectors":[{"name":s["name"],"status":s["status"],"score":s["score"],"raw_score":s.get("raw_score",s["score"]),
        "action":s.get("action"),"etfs":s["etfs"],"rank":s.get("rank"),"confidence":s.get("confidence"),"trend":s.get("trend"),
        "buy_signal_days":s.get("buy_signal_days",0),"stage_days":s.get("stage_days",0),
        "signal_story":s.get("signal_story",""),"factors":s.get("factors")} for s in sectors]}
    for old in sorted(days.keys())[:-120]: days.pop(old,None)
    write_json_atomic(HISTORY, history)

def main():
    previous_dashboard = load_previous_dashboard()
    now_utc = datetime.now(UTC)
    now_kst = now_utc.astimezone(KST)
    now_et = now_utc.astimezone(ET)
    session, perspective = market_session(now_et)
    history = load_history()
    evaluate_signal_outcomes(history)
    calibration = walk_forward_calibration(history)

    market, quotes = [], {}
    for name, sym in MARKET.items():
        try:
            latest, prev, chg = latest_extended(sym)
        except Exception as e:
            print(f"quote warning {name}: {e}")
            latest, prev, chg = None, None, 0.0
        quotes[name] = (latest, prev, chg)
        if name != "VIX":
            value, change_text, direction = format_market(name, latest, prev, chg)
            market.append({
                "name": name, "symbol": sym, "value": value,
                "change_pct": round(chg or 0, 2), "change_text": change_text,
                "change_direction": round(direction or 0, 4),
                "spark": spark_values(sym),
                "sources": market_sources(name, sym),
            })

    try:
        un = fred_series("UNRATE"); ur = float(un.iloc[-1].value); ur3 = float(un.iloc[-4].value); du = ur - ur3
        emp_state = "냉각" if du >= .2 else "강함"; emp_summary = f"실업률 {ur:.1f}% · 3개월 {du:+.1f}%p"
        emp_grade = "주의" if du >= .3 else "중립"
        emp_hint = "고용 둔화가 빨라지면 경기민감주에는 부담. 견조한 고용은 경기에 긍정적이나 금리인하 기대를 늦출 수 있음."
    except Exception:
        emp_state, emp_summary, emp_grade, emp_hint = "중립", "UNRATE 연결 오류", "중립", "고용 데이터 확인 필요"

    try:
        cp = fred_series("CPIAUCSL"); vals = cp["value"]; y = (vals.iloc[-1]/vals.iloc[-13]-1)*100; yp = (vals.iloc[-2]/vals.iloc[-14]-1)*100
        inf_state = "냉각" if y < yp else "부담"; inf_summary = f"CPI YoY {y:.2f}% · 이전 {yp:.2f}%"
        inf_grade = "우호" if y < yp else "주의"
        inf_hint = "물가 둔화는 장기금리 안정에 도움 → 성장주·바이오·리츠에 상대적으로 우호." if inf_grade == "우호" else "물가 재가속은 금리 상승 압력 → 고밸류 성장주·리츠에 부담."
    except Exception:
        inf_state, inf_summary, inf_grade, inf_hint = "중립", "CPI 연결 오류", "중립", "물가 데이터 확인 필요"

    ten_latest, ten_prev, _ = quotes.get("미 10년물", (None, None, 0))
    ten_y = tnx_yield(ten_latest); ten_prev_y = tnx_yield(ten_prev) if ten_prev is not None else ten_y
    ten_bp = (ten_y-ten_prev_y)*100 if ten_y is not None and ten_prev_y is not None else 0
    rate_state = "부담" if (ten_y and ten_y >= 4.5) or ten_bp >= 5 else "중립"
    rate_summary = f"10년물 {ten_y:.3f}% · 당일 {ten_bp:+.1f}bp" if ten_y is not None else "10년물 연결 오류"
    rate_grade = "주의" if rate_state == "부담" else "중립"
    rate_hint = "장기금리가 높거나 상승하면 QQQ·반도체·바이오·리츠 같은 금리민감 자산에 부담." if rate_grade == "주의" else "금리 부담이 급격히 확대되지 않는 중립 구간."

    try:
        cl = fred_series("ICSA"); c0 = float(cl.iloc[-1].value); c4 = float(cl.iloc[-5].value); ratio = c0/c4-1
        econ_state = "냉각" if ratio > .08 else "중립"; econ_summary = f"신규 실업수당 {c0/1000:.0f}K · 4주전 대비 {ratio*100:+.1f}%"
        econ_grade = "주의" if ratio > .12 else "중립"
        econ_hint = "실업수당 급증은 경기민감·소비 관련 자산에 부담. 단일 지표이므로 보조 신호로 사용."
    except Exception:
        econ_state, econ_summary, econ_grade, econ_hint = "중립", "실업수당 연결 오류", "중립", "경기 데이터 확인 필요"

    dxy = quotes.get("DXY", (None,None,0))[2] or 0
    liq_state = "개선" if dxy < -.3 else "부담" if dxy > .3 else "중립"; liq_summary = f"DXY 당일 {dxy:+.2f}%"
    liq_grade = "우호" if liq_state == "개선" else "주의" if liq_state == "부담" else "중립"
    liq_hint = "달러 약세는 글로벌 위험자산·원자재 유동성에 상대적으로 우호." if liq_grade == "우호" else "달러 강세는 글로벌 유동성과 원자재·신흥 위험자산에 부담." if liq_grade == "주의" else "달러 흐름이 중립 범위."

    vix = quotes.get("VIX", (20,None,0))[0] or 20
    risk_state = "위험회피" if vix >= 25 else "주의" if vix >= 20 else "강함"; risk_summary = f"VIX {vix:.1f}"
    risk_grade = "위험" if vix >= 30 else "주의" if vix >= 25 else "중립" if vix >= 18 else "우호"
    risk_hint = "VIX가 낮을수록 위험선호에 우호. 급등 시 고베타·레버리지 자산 변동성 확대에 주의."

    macro = [
        {"name":"고용","state":emp_state,"summary":emp_summary,"investment_grade":emp_grade,"investment_score":grade_score(emp_grade),"investment_hint":emp_hint,"sources":[{"label":"FRED · UNRATE","note":"미국 실업률 공식 시계열","url":fred_url("UNRATE")},{"label":"BLS","note":"미 노동통계국 고용 원문","url":"https://www.bls.gov/cps/"}]},
        {"name":"물가","state":inf_state,"summary":inf_summary,"investment_grade":inf_grade,"investment_score":grade_score(inf_grade),"investment_hint":inf_hint,"sources":[{"label":"FRED · CPIAUCSL","note":"CPI 공식 시계열","url":fred_url("CPIAUCSL")},{"label":"BLS CPI","note":"CPI 원문","url":"https://www.bls.gov/cpi/"}]},
        {"name":"금리","state":rate_state,"summary":rate_summary,"investment_grade":rate_grade,"investment_score":grade_score(rate_grade),"investment_hint":rate_hint,"sources":[{"label":"FRED · DGS10","note":"미국 10년 국채금리","url":fred_url("DGS10")},{"label":"U.S. Treasury","note":"미 재무부 금리 자료","url":"https://home.treasury.gov/resource-center/data-chart-center/interest-rates"}]},
        {"name":"경기","state":econ_state,"summary":econ_summary,"investment_grade":econ_grade,"investment_score":grade_score(econ_grade),"investment_hint":econ_hint,"sources":[{"label":"FRED · ICSA","note":"신규 실업수당","url":fred_url("ICSA")},{"label":"U.S. DOL","note":"실업보험 통계","url":"https://www.dol.gov/ui/data.pdf"}]},
        {"name":"유동성","state":liq_state,"summary":liq_summary,"investment_grade":liq_grade,"investment_score":grade_score(liq_grade),"investment_hint":liq_hint,"sources":[{"label":"ICE","note":"U.S. Dollar Index 공식 시장","url":"https://www.ice.com/products/194/US-Dollar-Index-Futures"},{"label":"Yahoo Finance","note":"DXY 시세","url":yahoo_url("DX-Y.NYB")}]},
        {"name":"위험선호","state":risk_state,"summary":risk_summary,"investment_grade":risk_grade,"investment_score":grade_score(risk_grade),"investment_hint":risk_hint,"sources":[{"label":"Cboe VIX","note":"VIX 공식 자료","url":"https://www.cboe.com/tradable_products/vix/"},{"label":"Yahoo Finance","note":"VIX 시세","url":yahoo_url("^VIX")}]},
    ]
    env = investment_environment(macro)

    spy = daily_history(BASE, "1y"); spy_ext, _, _ = latest_extended(BASE)
    timing = market_timing_signals(spy, spy_ext, vix, env.get("score", 55))
    breadth_score, breadth_metrics = market_breadth_score(spy, spy_ext)
    market_regime = market_regime_profile(spy, spy_ext, vix, breadth_score, env.get("score",55))
    events = official_calendar(now_et)
    event_risk = event_risk_profile(events, now_kst)
    # History is keyed by U.S. market date, not KST calendar date.
    # This prevents one U.S. session from being counted as two separate "days" after midnight in Korea.
    today = now_et.strftime("%Y-%m-%d")

    sectors = []
    for name, etfs in SECTORS:
        try:
            calc = calc_sector(etfs[0], spy, spy_ext_price=spy_ext, ten_y_change=ten_bp/100, vix=vix, breadth_score=breadth_score, session=session, factor_weights=calibration.get("weights"))
            participation = sector_participation_score(name, spy)
            sector = enrich_sector_signal({"name":name,"etfs":etfs,"quote_sources":etf_sources(etfs[0]),**calc}, history, today, now_et, market_regime, event_risk, participation)
        except Exception as e:
            sector = {"name":name,"etfs":etfs,"quote_sources":etf_sources(etfs[0]),"status":"중립","score":50,"raw_score":50,"action":"데이터 확인","reason":str(e),
                      "factors":{"momentum":50,"relative_strength":50,"volume":50,"breadth":50,"macro":50},"metrics":{},"trend":{"label":"→ 유지","avg3":50,"avg5":50,"delta_3d":0,"peak_drop_5d":0,"series":[50]},
                      "confidence":{"score":20,"grade":"D"},"data_quality":{"score":20,"grade":"D","flags":["계산 오류"]},"participation":{"score":50},"signal_days":0,"flags":["계산 오류"]}
        sectors.append(sector)
    sectors = assign_sector_ranks(sectors, history, today)
    changes = compute_changes(sectors, history, today)
    record_signal_entries(history, sectors, today, market_regime)
    scorecard = signal_scorecard(history, calibration)

    slowing = (emp_state == "냉각") + (econ_state == "냉각")
    phase = "둔화 관찰" if slowing >= 1 else "확장/중립"
    reasons = [f"시장 레짐 · {market_regime['label']} {market_regime['score']}점", f"고용 · {emp_summary}", f"물가 · {inf_summary}", f"금리 · {rate_summary}", f"위험선호 · {risk_summary}"]

    out = {
        "updated_at_kst": now_kst.strftime("%Y-%m-%d %H:%M"),
        "meta": {
            "updated_at_iso": now_kst.isoformat(timespec="seconds"),
            "updated_at_utc_iso": now_utc.isoformat(timespec="seconds"),
            "updated_at_kst": now_kst.strftime("%Y-%m-%d %H:%M"),
            "updated_at_et": now_et.strftime("%Y-%m-%d %H:%M"),
            "market_session": session,
            "perspective": perspective,
            "update_policy": "서버: 미국 거래일 기준 KST 17:00~익일 09:00, 30분 간격 · 프리마켓 시작부터 정규장·애프터마켓까지 수집 · 화면: 5분마다 새 데이터 자동 확인 · GitHub Actions는 지연 가능",
            "cautions": [
                "수집 시점과 미국장 세션을 먼저 확인하세요.",
                "Yahoo Finance extended-hours는 무료 비공식 데이터로 지연·누락이 있을 수 있습니다.",
                "프리/애프터마켓은 거래량이 얇아 왜곡될 수 있어 산업 레이더 보정폭을 최대 ±4점으로 제한합니다.",
                "투자환경 등급과 산업 점수는 미래 수익률 확률이 아닌 규칙 기반 보조 신호입니다.",
                "FRED·BLS·Census·Federal Reserve 일정은 공식 공개자료를 사용하지만 기관의 일정 변경이 있을 수 있습니다.",
                "외부 원자료·시세 사이트와 대시보드의 수집 시점이 달라 값이 다를 수 있습니다.",
                "CNN Fear & Greed는 CNN 페이지가 사용하는 공개 JSON 데이터를 읽습니다. 구조 변경·차단 시 일시적으로 미표시될 수 있습니다.",
                "저점매수 매력도는 싸다는 보장이 아니라 공포·낙폭·과매도 정도를 정량화한 역발상 보조 지표입니다.",
                "반전 확인도는 바닥 확정 신호가 아니라 모멘텀·20일선·VIX·시장 폭·거래량 회복 정도를 보는 보조 지표입니다.",
                "시장 Breadth는 RSP/SPY·QQQE/QQQ 상대강도를 이용한 무료 프록시이며 전체 종목 상승/하락 종목수를 직접 집계한 값은 아닙니다.",
                "산업 내부 확산은 대표 ETF 묶음의 참여도를 보는 프록시이며 개별 구성종목 전수 상승/하락 집계가 아닙니다.",
                "레이더 표시는 현재 원점수 60% + 최근 3일 평균 40%로 안정화하고, 진입·이탈 기준을 다르게 둬 하루 노이즈를 줄입니다.",
                "신뢰도 점수는 데이터 품질·내부 확산·팩터 일치·지속성·시장 레짐을 합친 규칙 기반 등급이며 실제 성공확률을 뜻하지 않습니다.",
                "장외 움직임만으로 상태가 바뀌면 잠정 신호로 표시하며 정규장 확인 전 과도한 해석을 피합니다.",
                "Walk-forward 가중치 보정은 20거래일 결과가 60건 쌓이기 전까지 비활성화되며, 이후에도 소폭만 조정합니다.",
                "현재 버전은 뉴스·기업 실적·가이던스의 의미를 자동 점수에 완전히 반영하지 않습니다.",
            ],
        },
        "market": market,
        "regime": {"name": phase, "environment": env, "market_regime": market_regime, "reasons": reasons},
        "macro": macro,
        "timing": timing,
        "breadth": {"score": round(float(breadth_score), 1), "metrics": breadth_metrics, "note": "RSP/SPY + QQQE/QQQ 동일가중 대비 시총가중 상대강도 프록시"},
        "signal_scorecard": scorecard,
        "calibration": calibration,
        "event_risk": event_risk,
        "sectors": sectors,
        "changes": changes,
        "events": events,
    }
    if telegram_test_message(now_kst):
        print("telegram: test completed")
    else:
        notify_new_signals(previous_dashboard, sectors, timing, now_kst, history)

    save_history(history, today, sectors, market_regime)
    write_json_atomic(OUT, out)
    print(f"Wrote {OUT}")
    print(f"Wrote {HISTORY}")
    print(f"Perspective: {perspective}")
    print(f"Calendar events: {len(events)}")


if __name__ == "__main__":
    main()
