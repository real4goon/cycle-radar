from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, time
from zoneinfo import ZoneInfo
from io import StringIO

import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "dashboard.json"

MARKET = {
    "나스닥 선물": "NQ=F",
    "S&P500 선물": "ES=F",
    "SOX": "^SOX",
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


def clamp(x, lo=0, hi=100):
    return max(lo, min(hi, x))


def daily_history(symbol, period="6mo"):
    d = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
    if d is None or d.empty:
        raise RuntimeError(f"No daily data for {symbol}")
    return d


def latest_extended(symbol):
    """Return latest extended-hours price, previous regular close, pct change."""
    t = yf.Ticker(symbol)
    intr = t.history(period="1d", interval="5m", prepost=True, auto_adjust=True)
    daily = t.history(period="5d", interval="1d", auto_adjust=True)
    if daily.empty:
        return None, None, 0.0
    prev = float(daily["Close"].iloc[-2] if len(daily) >= 2 else daily["Close"].iloc[-1])
    if intr is not None and not intr.empty and not intr["Close"].dropna().empty:
        latest = float(intr["Close"].dropna().iloc[-1])
    else:
        latest = float(daily["Close"].iloc[-1])
    chg = (latest / prev - 1) * 100 if prev else 0.0
    return latest, prev, chg


def fred_series(sid):
    r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=30)
    r.raise_for_status()
    d = pd.read_csv(StringIO(r.text))
    d.columns = ["date", "value"]
    d["value"] = pd.to_numeric(d["value"], errors="coerce")
    return d.dropna()


def market_session(now_et: datetime):
    if now_et.weekday() >= 5:
        return "주말/휴장", "미국장 휴장 시점의 마지막 데이터 관점"
    t = now_et.time()
    if time(4, 0) <= t < time(9, 30):
        return "프리마켓", "미국 정규장 개장 전(프리마켓) 관점"
    if time(9, 30) <= t < time(16, 0):
        return "정규장", "미국 정규장 진행 중 관점"
    if time(16, 0) <= t < time(20, 0):
        return "애프터마켓", "미국 정규장 마감 후(애프터마켓) 관점"
    return "장 외 시간", "미국장 외 시간의 마지막 데이터 관점"


def calc_sector(primary, spy_df, spy_ext_price=None, ten_y_change=0, vix=20):
    d = daily_history(primary)
    c = d["Close"].dropna()
    v = d["Volume"].fillna(0)

    ext_price, _, ext_chg = latest_extended(primary)
    px = ext_price if ext_price else float(c.iloc[-1])

    # extended-hours price is incorporated into price momentum / relative strength.
    r5 = (px / c.iloc[-5] - 1) * 100 if len(c) >= 5 else 0
    r20 = (px / c.iloc[-20] - 1) * 100 if len(c) >= 20 else 0

    sp = spy_df["Close"].dropna()
    spy_px = spy_ext_price if spy_ext_price else float(sp.iloc[-1])
    sp20 = (spy_px / sp.iloc[-20] - 1) * 100 if len(sp) >= 20 else 0
    rs = r20 - sp20

    v20 = v.tail(20).mean() if len(v) >= 20 else 0
    vr = (v.tail(5).mean() / v20) if v20 else 1

    mom = clamp(50 + r5 * 3 + r20 * 1.3)
    rel = clamp(50 + rs * 5)
    vol = clamp(50 + (vr - 1) * 70)

    macro = 55
    rate_sensitive = primary in {"QQQM", "SMH", "SOXX", "XBI", "IBB", "VNQ"}
    defensive = primary in {"XLP", "GLD", "GDX"}
    cyclical = primary in {"XLI", "XLB", "XLE", "XLF"}
    if rate_sensitive:
        macro += -10 if ten_y_change > 0.05 else 7 if ten_y_change < -0.05 else 0
    if defensive and vix > 22:
        macro += 10
    if cyclical and vix < 22:
        macro += 5
    macro = clamp(macro)

    total = round(mom * 0.35 + rel * 0.30 + vol * 0.20 + macro * 0.15)
    status = "상승 사이클" if total >= 75 else "초기 관심" if total >= 60 else "중립" if total >= 45 else "약화"
    action = (
        "추격주의 · 조정 대기" if status == "상승 사이클" and r5 > 5
        else "분할 접근" if status == "상승 사이클"
        else "소액 진입 검토" if status == "초기 관심" and r5 < 3
        else "관찰 · 조정 대기" if status == "초기 관심"
        else "추가매수 보류" if status == "약화"
        else "관찰"
    )
    reason = f"프리/정규 최신가 반영 · 당일 {ext_chg:+.1f}% · 5일 {r5:+.1f}% · 20일 {r20:+.1f}% · SPY 대비 20일 {rs:+.1f}%p · 5/20일 거래량 {vr:.2f}배"
    return {
        "status": status,
        "score": int(total),
        "action": action,
        "reason": reason,
        "factors": {
            "momentum": round(mom),
            "relative_strength": round(rel),
            "volume": round(vol),
            "macro": round(macro),
        },
    }


def format_market(name, latest, prev, chg_pct):
    if latest is None:
        return "-", "-", 0.0

    if name == "미 10년물":
        # Yahoo ^TNX is quoted as yield x 10 (e.g. 46.98 -> 4.698%).
        y = latest / 10.0
        p = prev / 10.0 if prev else y
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
    return value, f"{chg_pct:+.2f}%", chg_pct


def main():
    now_utc = datetime.now(ZoneInfo("UTC"))
    now_kst = now_utc.astimezone(ZoneInfo("Asia/Seoul"))
    now_et = now_utc.astimezone(ZoneInfo("America/New_York"))
    session, perspective = market_session(now_et)

    market = []
    quotes = {}
    for name, sym in MARKET.items():
        try:
            latest, prev, chg = latest_extended(sym)
        except Exception:
            latest, prev, chg = None, None, 0.0
        quotes[name] = (latest, prev, chg)
        if name != "VIX":
            value, change_text, direction = format_market(name, latest, prev, chg)
            market.append({
                "name": name,
                "value": value,
                "change_pct": round(chg or 0, 2),
                "change_text": change_text,
                "change_direction": round(direction or 0, 4),
            })

    try:
        un = fred_series("UNRATE")
        ur = float(un.iloc[-1].value)
        ur3 = float(un.iloc[-4].value)
        emp_state = "냉각" if ur - ur3 >= 0.2 else "강함"
        emp_summary = f"실업률 {ur:.1f}% · 3개월 {ur-ur3:+.1f}%p"
    except Exception:
        emp_state, emp_summary = "중립", "UNRATE 연결 오류"

    try:
        cp = fred_series("CPIAUCSL")
        vals = cp["value"]
        y = (vals.iloc[-1] / vals.iloc[-13] - 1) * 100
        yp = (vals.iloc[-2] / vals.iloc[-14] - 1) * 100
        inf_state = "냉각" if y < yp else "부담"
        inf_summary = f"CPI YoY {y:.2f}% · 이전 {yp:.2f}%"
    except Exception:
        inf_state, inf_summary = "중립", "CPI 연결 오류"

    ten_latest, ten_prev, _ = quotes.get("미 10년물", (None, None, 0))
    if ten_latest is not None:
        ten_y = ten_latest / 10
        ten_prev_y = ten_prev / 10 if ten_prev else ten_y
        ten_bp = (ten_y - ten_prev_y) * 100
    else:
        ten_y, ten_bp = None, 0
    rate_state = "부담" if ten_bp >= 5 else "중립"
    rate_summary = f"10년물 {ten_y:.3f}% · 당일 {ten_bp:+.1f}bp" if ten_y is not None else "10년물 연결 오류"

    try:
        cl = fred_series("ICSA")
        c0 = float(cl.iloc[-1].value)
        c4 = float(cl.iloc[-5].value)
        econ_state = "냉각" if c0 > c4 * 1.08 else "중립"
        econ_summary = f"신규 실업수당 {c0/1000:.0f}K · 4주전 대비 {(c0/c4-1)*100:+.1f}%"
    except Exception:
        econ_state, econ_summary = "중립", "실업수당 연결 오류"

    dxy = quotes.get("DXY", (None, None, 0))[2] or 0
    liq_state = "개선" if dxy < -0.3 else "부담" if dxy > 0.3 else "중립"
    liq_summary = f"DXY 당일 {dxy:+.2f}%"

    vix = quotes.get("VIX", (20, None, 0))[0] or 20
    risk_state = "위험회피" if vix >= 25 else "주의" if vix >= 20 else "강함"
    risk_summary = f"VIX {vix:.1f}"

    macro = [
        {"name": "고용", "state": emp_state, "summary": emp_summary},
        {"name": "물가", "state": inf_state, "summary": inf_summary},
        {"name": "금리", "state": rate_state, "summary": rate_summary},
        {"name": "경기", "state": econ_state, "summary": econ_summary},
        {"name": "유동성", "state": liq_state, "summary": liq_summary},
        {"name": "위험선호", "state": risk_state, "summary": risk_summary},
    ]

    spy = daily_history(BASE)
    spy_ext, _, _ = latest_extended(BASE)
    ten_signal = ten_bp / 100
    sectors = []
    for name, etfs in SECTORS:
        try:
            calc = calc_sector(etfs[0], spy, spy_ext_price=spy_ext, ten_y_change=ten_signal, vix=vix)
        except Exception as e:
            calc = {
                "status": "중립", "score": 50, "action": "데이터 확인", "reason": str(e),
                "factors": {"momentum": 50, "relative_strength": 50, "volume": 50, "macro": 50},
            }
        sectors.append({"name": name, "etfs": etfs, **calc})

    watch = [
        {"symbol": s["etfs"][0], "name": s["name"], "status": s["status"], "action": s["action"]}
        for s in sorted(sectors, key=lambda x: x["score"], reverse=True)[:4]
    ]

    riskoff = (vix >= 20) + (rate_state == "부담") + (liq_state == "부담")
    slowing = (emp_state == "냉각") + (econ_state == "냉각")
    regime = "Risk-Off / 금리·변동성 부담" if riskoff >= 2 else "Late Expansion / 둔화 관찰" if slowing >= 1 else "Risk-On / 확장"

    out = {
        "updated_at_kst": now_kst.strftime("%Y-%m-%d %H:%M"),
        "meta": {
            "updated_at_iso": now_utc.isoformat().replace("+00:00", "Z"),
            "updated_at_kst": now_kst.strftime("%Y-%m-%d %H:%M"),
            "updated_at_et": now_et.strftime("%Y-%m-%d %H:%M"),
            "market_session": session,
            "perspective": perspective,
            "update_policy": "서버: 평일 20:00~23:30 KST 30분 간격 · 화면: 5분마다 새 데이터 자동 확인 · GitHub Actions는 지연 가능",
            "cautions": [
                "수집 시점과 미국장 세션(프리마켓/정규장/애프터마켓)을 먼저 확인하세요.",
                "Yahoo Finance extended-hours는 무료 비공식 데이터로 실시간 체결가와 지연·누락 차이가 있을 수 있습니다.",
                "산업 점수는 미래 수익률 확률이 아니라 가격 모멘텀·SPY 대비 상대강도·거래량·Macro 환경의 규칙 기반 점수입니다.",
                "FRED 거시지표는 각 지표의 최신 공식 발표값이며 장중 실시간 수치가 아닙니다.",
                "GitHub Actions 예약 실행은 지정 시각보다 늦게 시작될 수 있으므로 화면의 실제 수집 시점을 기준으로 판단하세요.",
                "현재 버전은 뉴스·기업 실적·가이던스의 의미를 자동 점수에 완전히 반영하지 않습니다. 중대한 이벤트는 별도 확인이 필요합니다."
            ]
        },
        "market": market,
        "regime": {"name": regime, "reasons": [emp_summary, rate_summary, risk_summary]},
        "macro": macro,
        "sectors": sectors,
        "watchlist": watch,
        "events": [
            {"name": "CPI", "when": "발표 일정 자동화는 다음 단계"},
            {"name": "NFP", "when": "발표 일정 자동화는 다음 단계"},
            {"name": "실업률", "when": "NFP와 동시 발표"},
            {"name": "소매판매", "when": "발표 일정 자동화는 다음 단계"},
            {"name": "FOMC", "when": "일정 자동화는 다음 단계"},
            {"name": "실업수당", "when": "매주 목요일"},
        ],
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"Perspective: {perspective}")


if __name__ == "__main__":
    main()
