"""Fail-closed intersection evaluation and durable, at-most-once Telegram dispatch."""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data/telegram_state.json"
MAX_SOURCE_HOURS = 36
MAX_DASHBOARD_HOURS = 2


def timestamp(value):
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def fresh(value, now, hours):
    dt = timestamp(value)
    return dt is not None and -300 <= (now - dt).total_seconds() <= hours * 3600


def evaluate(dashboard, now):
    timing = dashboard.get("timing") or {}
    fg = timing.get("fear_greed") or {}
    metrics = timing.get("metrics") or {}
    sources = timing.get("source_as_of") or {}
    values = [fg.get("score"), metrics.get("vix"), metrics.get("spy_rsi14")]
    valid_values = all(type(x) in (float, int) and math.isfinite(x) for x in values)
    valid_values = valid_values and 0 <= values[0] <= 100 and 0 < values[1] <= 1000 and 0 <= values[2] <= 100
    reasons = []
    if not valid_values or fg.get("available") is not True:
        reasons.append("지표 누락 또는 유효 범위 오류")
    if not fresh((dashboard.get("meta") or {}).get("updated_at_utc_iso"), now, MAX_DASHBOARD_HOURS):
        reasons.append("대시보드 수집 시각 누락 또는 2시간 초과")
    for name, value in [("fear_greed", fg.get("timestamp")), ("vix", sources.get("vix")), ("spy_rsi14", sources.get("spy_rsi14"))]:
        if not fresh(value, now, MAX_SOURCE_HOURS):
            reasons.append(f"{name} 원자료 시각 누락 또는 36시간 초과")
    checks = [values[0] <= 9, values[1] >= 28, values[2] <= 30] if valid_values else [False] * 3
    return {"valid": not reasons, "count": sum(checks) if valid_values else None,
            "active": not reasons and all(checks), "values": values,
            "checks": checks, "reasons": reasons, "evaluated_at": now.isoformat(),
            "source_max_age_hours": MAX_SOURCE_HOURS, "dashboard_max_age_hours": MAX_DASHBOARD_HOURS}


def atomic_write(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_state(path):
    if not path.exists():
        return {"version": 1, "active": False}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("version") != 1 or type(state.get("active")) is not bool:
        raise ValueError("Invalid Telegram state; manual recovery required")
    return state


def prepare(result, state, attempt, configured):
    """Invalid data preserves the latch. Only a valid release rearms it."""
    state = dict(state)
    if not result["valid"]:
        return state, False
    if not result["active"]:
        return {"version": 1, "active": False}, False
    if state["active"] or not configured:
        return state, False
    return {"version": 1, "active": True, "delivery": "reserved", "attempt": attempt,
            "reserved_at": result["evaluated_at"], "values": result["values"]}, True


def telegram_send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        raise RuntimeError("Telegram secrets are missing")
    request = Request(f"https://api.telegram.org/bot{token}/sendMessage",
                      data=json.dumps({"chat_id": chat, "text": text,
                                       "link_preview_options": {"is_disabled": True}}).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    # No automatic retries: timeout may mean Telegram accepted the message.
    try:
        with urlopen(request, timeout=20) as response:
            data = json.load(response)
        if data.get("ok") is not True:
            raise RuntimeError("rejected")
    except Exception:
        # Exceptions may contain the credential-bearing URL. Never print them.
        raise RuntimeError("Telegram delivery failed or is uncertain; reservation retained") from None


def deliver(result, state, attempt, send=telegram_send):
    if state.get("delivery") != "reserved" or state.get("attempt") != attempt:
        raise RuntimeError("No reservation owned by this workflow attempt")
    if not result["active"]:
        raise RuntimeError("Data is no longer valid and active; dispatch blocked")
    fg, vix, rsi = result["values"]
    send(f"🚨 미국 산업 사이클 레이더 · 극단적 공포 교집합 3/3\n"
         f"Fear & Greed {fg} ≤ 9\nVIX {vix} ≥ 28\nSPY RSI(14) {rsi} ≤ 30\n"
         f"확인 시각(UTC): {result['evaluated_at']}\n"
         "https://real4goon.github.io/cycle-radar/")
    return {**state, "delivery": "sent", "sent_at": result["evaluated_at"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "deliver"])
    args = parser.parse_args()
    attempt = os.environ["ALERT_ATTEMPT"]
    now = datetime.now(timezone.utc)
    dashboard_path = ROOT / "data/dashboard.json"
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    result = evaluate(dashboard, now)
    state = load_state(STATE)
    if args.mode == "prepare":
        dashboard["timing"]["intersection"] = result
        atomic_write(dashboard_path, dashboard)
        configured = all(os.environ.get(key, "").strip() for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"))
        state, should_send = prepare(result, state, attempt, configured)
        atomic_write(STATE, state)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write(f"should_send={str(should_send).lower()}\n")
        print(f"intersection: valid={result['valid']} count={result['count']} dispatch={should_send}")
        if not configured:
            print("::warning::Telegram Secrets are not configured; dispatch disabled")
    else:
        atomic_write(STATE, deliver(result, state, attempt))
        print("Telegram intersection message sent")


if __name__ == "__main__":
    main()
