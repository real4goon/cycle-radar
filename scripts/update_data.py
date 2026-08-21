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

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; cycle-radar/1.9; +https://github.com/)"}


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
        "1) 산업별 판단이 새로 ‘분할 접근’으로 변경될 때",
        "2) 시장 타이밍 판단이 새로 ‘분할 접근 검토’로 변경될 때",
        "",
        "• 동일 상태 유지 시 중복 알림 없음",
        "• 상태 이탈 후 다시 진입하면 재알림",
        "• 소액 진입 검토 / 관찰 / 조정 대기는 현재 알림 대상 아님",
        "",
        f"확인 시각: {now_kst.strftime('%Y-%m-%d %H:%M')} KST",
    ]
    if dashboard_url:
        lines += ["", f"대시보드: {dashboard_url}"]
    return telegram_send("\n".join(lines))


def notify_new_signals(previous, sectors, timing, now_kst):
    """Send only on a newly-entered target action, not every scheduled run."""
    if os.getenv("TELEGRAM_TEST", "false").lower() in {"1", "true", "yes", "on"}:
        return

    prev_sectors = {
        s.get("name"): s for s in (previous.get("sectors") or []) if isinstance(s, dict)
    }
    alerts = []

    for s in sectors:
        current_action = s.get("action")
        prev_action = (prev_sectors.get(s.get("name")) or {}).get("action")
        if current_action == "분할 접근" and prev_action != "분할 접근":
            alerts.append({
                "kind": "sector",
                "name": s.get("name", "산업"),
                "etfs": " / ".join(s.get("etfs") or []),
                "action": current_action,
                "score": s.get("score"),
                "status": s.get("status"),
                "previous": prev_action or "이전 기록 없음",
                "reason": s.get("reason", ""),
            })

    current_timing = timing.get("action") if isinstance(timing, dict) else None
    prev_timing = ((previous.get("timing") or {}).get("action") if isinstance(previous, dict) else None)
    if current_timing == "분할 접근 검토" and prev_timing != "분할 접근 검토":
        alerts.append({
            "kind": "timing",
            "action": current_timing,
            "previous": prev_timing or "이전 기록 없음",
            "low_buy": (timing.get("low_buy") or {}).get("score"),
            "reversal": (timing.get("reversal") or {}).get("score"),
            "summary": timing.get("summary", ""),
        })

    if not alerts:
        print("telegram: no new target signal")
        return

    dashboard_url = os.getenv("DASHBOARD_URL", "").strip()
    for a in alerts:
        if a["kind"] == "sector":
            lines = [
                "🚨 미국 산업 사이클 레이더",
                "",
                f"🟢 {a['name']}  {a['etfs']}",
                f"오늘의 판단: {a['action']}",
                f"사이클: {a['status']} · 레이더 {a['score']}점",
                f"이전 판단: {a['previous']}",
            ]
            if a.get("reason"):
                lines += ["", a["reason"]]
        else:
            lines = [
                "🚨 미국 시장 타이밍 신호",
                "",
                f"판단: {a['action']}",
                f"저점매수 매력도: {a['low_buy']}점",
                f"반전 확인도: {a['reversal']}점",
                f"이전 판단: {a['previous']}",
            ]
            if a.get("summary"):
                lines += ["", a["summary"]]
        lines += [
            "",
            f"{now_kst.strftime('%Y-%m-%d %H:%M')} KST",
            "알림 기준: 신규 ‘분할 접근’ / 신규 ‘분할 접근 검토’",
        ]
        if dashboard_url:
            lines += [f"대시보드: {dashboard_url}"]
        telegram_send("\n".join(lines))


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
        return "closed", "주말 · 미국장 휴장 시점 관점"
    mins = now_et.hour * 60 + now_et.minute
    if 4 * 60 <= mins < 9 * 60 + 30:
        return "premarket", "미국장 프리마켓 관점"
    if 9 * 60 + 30 <= mins < 16 * 60:
        return "regular", "미국장 정규장 진행 중 관점"
    if 16 * 60 <= mins < 20 * 60:
        return "afterhours", "미국장 애프터마켓 관점"
    return "closed", "미국장 외 시간의 마지막 데이터 관점"


def calc_sector(primary, spy_df, spy_ext_price=None, ten_y_change=0, vix=20):
    df = daily_history(primary, "6mo")
    close = df["Close"].dropna().copy()
    vol = df["Volume"].fillna(0)
    ext, _, extchg = latest_extended(primary)
    if ext is not None and len(close):
        close.iloc[-1] = ext
    spy = spy_df["Close"].dropna().copy()
    if spy_ext_price is not None and len(spy):
        spy.iloc[-1] = spy_ext_price

    r5 = (close.iloc[-1] / close.iloc[-6] - 1) * 100 if len(close) > 6 else 0
    r20 = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) > 21 else 0
    spy20 = (spy.iloc[-1] / spy.iloc[-21] - 1) * 100 if len(spy) > 21 else 0
    rs = r20 - spy20
    v5 = vol.tail(5).mean()
    v20 = vol.tail(20).mean() if len(vol) >= 20 else max(v5, 1)
    vr = v5 / v20 if v20 else 1

    mom = clamp(50 + r5 * 3 + r20 * 1.3)
    rel = clamp(50 + rs * 5)
    volume = clamp(50 + (vr - 1) * 70)
    macro = 55
    if primary in {"QQQM", "SMH", "SOXX", "XBI", "IBB", "VNQ"}:
        macro += -10 if ten_y_change > 0.05 else (7 if ten_y_change < -0.05 else 0)
    if primary in {"XLP", "GLD", "GDX"} and vix > 22:
        macro += 10
    if primary in {"XLI", "XLB", "XLE", "XLF"} and vix < 22:
        macro += 5
    macro = clamp(macro)

    total = round_int(mom * .35 + rel * .30 + volume * .20 + macro * .15)
    status = "상승 사이클" if total >= 75 else "초기 관심" if total >= 60 else "중립" if total >= 45 else "약화"
    if status == "상승 사이클" and r5 > 5:
        action = "추격주의 · 조정 대기"
    elif status == "상승 사이클":
        action = "분할 접근"
    elif status == "초기 관심" and r5 < 3:
        action = "소액 진입 검토"
    elif status == "초기 관심":
        action = "관찰 · 조정 대기"
    elif status == "약화":
        action = "추가매수 보류"
    else:
        action = "관찰"

    reason = (
        f"프리/정규 최신가 반영 · 당일 {extchg:+.1f}% · 5일 {r5:+.1f}% · "
        f"20일 {r20:+.1f}% · SPY 대비 20일 {rs:+.1f}%p · 5/20일 거래량 {vr:.2f}배"
    )
    return {
        "status": status,
        "score": total,
        "action": action,
        "reason": reason,
        "factors": {
            "momentum": round_int(mom),
            "relative_strength": round_int(rel),
            "volume": round_int(volume),
            "macro": round_int(macro),
        },
    }


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


# ---------- daily change tracking ----------

def load_history():
    if not HISTORY.exists():
        return {"days": {}}
    try:
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    except Exception:
        return {"days": {}}


def change_rank(status):
    return {"약화": 0, "중립": 1, "초기 관심": 2, "상승 사이클": 3}.get(status, 1)


def compute_changes(sectors, history, today):
    days = history.get("days", {})
    previous_dates = sorted([d for d in days.keys() if d < today])
    if not previous_dates:
        return {"improved": [], "worsened": [], "unchanged": [s["etfs"][0] for s in sectors], "note": "전일 비교 데이터 적재 중 · 다음 거래일부터 상태 변화를 표시합니다."}
    prev_date = previous_dates[-1]
    prev = {x["name"]: x for x in days[prev_date].get("sectors", [])}
    improved, worsened, unchanged = [], [], []
    for s in sectors:
        p = prev.get(s["name"])
        if not p:
            unchanged.append(s["etfs"][0]); continue
        delta = int(s["score"]) - int(p.get("score", s["score"]))
        rdelta = change_rank(s["status"]) - change_rank(p.get("status", "중립"))
        obj = {"name": s["name"], "symbol": s["etfs"][0], "from_status": p.get("status", "-"), "to_status": s["status"], "delta": delta}
        if rdelta > 0 or (rdelta == 0 and delta >= 5):
            improved.append(obj)
        elif rdelta < 0 or (rdelta == 0 and delta <= -5):
            worsened.append(obj)
        else:
            unchanged.append(s["etfs"][0])
    improved.sort(key=lambda x: x["delta"], reverse=True)
    worsened.sort(key=lambda x: x["delta"])
    return {"improved": improved, "worsened": worsened, "unchanged": unchanged, "note": f"비교 기준: {prev_date} 최종 저장값"}


def save_history(history, today, sectors):
    days = history.setdefault("days", {})
    days[today] = {"sectors": [{"name": s["name"], "status": s["status"], "score": s["score"], "etfs": s["etfs"]} for s in sectors]}
    for old in sorted(days.keys())[:-14]:
        days.pop(old, None)
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    previous_dashboard = load_previous_dashboard()
    now_utc = datetime.now(UTC)
    now_kst = now_utc.astimezone(KST)
    now_et = now_utc.astimezone(ET)
    session, perspective = market_session(now_et)

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
    sectors = []
    for name, etfs in SECTORS:
        try:
            calc = calc_sector(etfs[0], spy, spy_ext_price=spy_ext, ten_y_change=ten_bp/100, vix=vix)
        except Exception as e:
            calc = {"status":"중립","score":50,"action":"데이터 확인","reason":str(e),"factors":{"momentum":50,"relative_strength":50,"volume":50,"macro":50}}
        sectors.append({"name": name, "etfs": etfs, "quote_sources": etf_sources(etfs[0]), **calc})

    history = load_history(); today = now_kst.strftime("%Y-%m-%d")
    changes = compute_changes(sectors, history, today)
    save_history(history, today, sectors)

    slowing = (emp_state == "냉각") + (econ_state == "냉각")
    phase = "둔화 관찰" if slowing >= 1 else "확장/중립"
    reasons = [f"고용 · {emp_summary}", f"물가 · {inf_summary}", f"금리 · {rate_summary}", f"위험선호 · {risk_summary}"]

    events = official_calendar(now_et)

    out = {
        "updated_at_kst": now_kst.strftime("%Y-%m-%d %H:%M"),
        "meta": {
            "updated_at_iso": now_kst.isoformat(timespec="seconds"),
            "updated_at_utc_iso": now_utc.isoformat(timespec="seconds"),
            "updated_at_kst": now_kst.strftime("%Y-%m-%d %H:%M"),
            "updated_at_et": now_et.strftime("%Y-%m-%d %H:%M"),
            "market_session": session,
            "perspective": perspective,
            "update_policy": "서버: 평일 20:00~23:30 KST 30분 간격 · 화면: 5분마다 새 데이터 자동 확인 · GitHub Actions는 지연 가능",
            "cautions": [
                "수집 시점과 미국장 세션을 먼저 확인하세요.",
                "Yahoo Finance extended-hours는 무료 비공식 데이터로 지연·누락이 있을 수 있습니다.",
                "투자환경 등급과 산업 점수는 미래 수익률 확률이 아닌 규칙 기반 보조 신호입니다.",
                "FRED·BLS·Census·Federal Reserve 일정은 공식 공개자료를 사용하지만 기관의 일정 변경이 있을 수 있습니다.",
                "외부 원자료·시세 사이트와 대시보드의 수집 시점이 달라 값이 다를 수 있습니다.",
                "CNN Fear & Greed는 CNN 페이지가 사용하는 공개 JSON 데이터를 읽습니다. 구조 변경·차단 시 일시적으로 미표시될 수 있습니다.",
                "저점매수 매력도는 싸다는 보장이 아니라 공포·낙폭·과매도 정도를 정량화한 역발상 보조 지표입니다.",
                "반전 확인도는 바닥 확정 신호가 아니라 모멘텀·20일선·VIX·시장 폭·거래량 회복 정도를 보는 보조 지표입니다.",
                "현재 버전은 뉴스·기업 실적·가이던스의 의미를 자동 점수에 완전히 반영하지 않습니다.",
            ],
        },
        "market": market,
        "regime": {"name": phase, "environment": env, "reasons": reasons},
        "macro": macro,
        "timing": timing,
        "sectors": sectors,
        "changes": changes,
        "events": events,
    }
    if telegram_test_message(now_kst):
        print("telegram: test completed")
    else:
        notify_new_signals(previous_dashboard, sectors, timing, now_kst)

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"Wrote {HISTORY}")
    print(f"Perspective: {perspective}")
    print(f"Calendar events: {len(events)}")


if __name__ == "__main__":
    main()
