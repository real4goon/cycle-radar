from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import requests
import yfinance as yf
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/"data"/"dashboard.json"
MARKET={"나스닥 선물":"NQ=F","S&P500 선물":"ES=F","SOX":"^SOX","WTI 원유":"CL=F","미 10년물":"^TNX","DXY":"DX-Y.NYB","비트코인":"BTC-USD","달러/원":"KRW=X","VIX":"^VIX"}
SECTORS=[("반도체/AI 하드웨어",["SMH","SOXX"]),("바이오/헬스케어",["XBI","IBB"]),("산업재",["XLI"]),("소재/원자재",["XLB"]),("에너지",["XLE"]),("금융",["XLF"]),("금/금광",["GLD","GDX"]),("빅테크",["QQQM"]),("필수소비재",["XLP"]),("리츠",["VNQ"])];BASE="SPY"
def clamp(x,lo=0,hi=100):return max(lo,min(hi,x))
def daily_history(symbol,period="6mo"):
 d=yf.Ticker(symbol).history(period=period,interval="1d",auto_adjust=True)
 if d is None or d.empty:raise RuntimeError(f"No daily data for {symbol}")
 return d
def latest_extended(symbol):
 t=yf.Ticker(symbol);intr=t.history(period="1d",interval="5m",prepost=True,auto_adjust=True);daily=t.history(period="5d",interval="1d",auto_adjust=True)
 if daily.empty:return None,None
 prev=float(daily["Close"].iloc[-2] if len(daily)>=2 else daily["Close"].iloc[-1]);latest=float(intr["Close"].dropna().iloc[-1]) if intr is not None and not intr.empty else float(daily["Close"].iloc[-1]);chg=(latest/prev-1)*100 if prev else 0;return latest,chg
def fred_series(sid):
 from io import StringIO
 r=requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}",timeout=30);r.raise_for_status();d=pd.read_csv(StringIO(r.text));d.columns=["date","value"];d["value"]=pd.to_numeric(d["value"],errors="coerce");return d.dropna()
def calc_sector(primary,spy_df,ten_y_change=0,vix=20):
 d=daily_history(primary);c=d["Close"].dropna();v=d["Volume"].fillna(0);r5=(c.iloc[-1]/c.iloc[-6]-1)*100 if len(c)>6 else 0;r20=(c.iloc[-1]/c.iloc[-21]-1)*100 if len(c)>21 else 0;sp=spy_df["Close"].dropna();sp20=(sp.iloc[-1]/sp.iloc[-21]-1)*100 if len(sp)>21 else 0;rs=r20-sp20;vr=(v.tail(5).mean()/v.tail(20).mean()) if len(v)>=20 and v.tail(20).mean() else 1;mom=clamp(50+r5*3+r20*1.3);rel=clamp(50+rs*5);vol=clamp(50+(vr-1)*70);macro=55;rate_sensitive=primary in {"QQQM","SMH","SOXX","XBI","IBB","VNQ"};defensive=primary in {"XLP","GLD","GDX"};cyclical=primary in {"XLI","XLB","XLE","XLF"};macro+=(-10 if ten_y_change>.05 else 7 if ten_y_change<-.05 else 0) if rate_sensitive else 0;macro+=10 if defensive and vix>22 else 0;macro+=5 if cyclical and vix<22 else 0;macro=clamp(macro);total=round(mom*.35+rel*.30+vol*.20+macro*.15);status="상승 사이클" if total>=75 else "초기 관심" if total>=60 else "중립" if total>=45 else "약화";action="추격주의 · 조정 대기" if status=="상승 사이클" and r5>5 else "분할 접근" if status=="상승 사이클" else "소액 진입 검토" if status=="초기 관심" and r5<3 else "관찰 · 조정 대기" if status=="초기 관심" else "추가매수 보류" if status=="약화" else "관찰";reason=f"5일 {r5:+.1f}%, 20일 {r20:+.1f}%, SPY 대비 20일 {rs:+.1f}%p, 5/20일 거래량 {vr:.2f}배";return {"status":status,"score":int(total),"action":action,"reason":reason,"factors":{"momentum":round(mom),"relative_strength":round(rel),"volume":round(vol),"macro":round(macro)}}
def fmt_market(name,value):
 if value is None:return "-"
 if name=="미 10년물":return f"{value:.3f}%"
 if name in {"WTI 원유","DXY"}:return f"{value:,.2f}"
 if name=="달러/원":return f"{value:,.1f}"
 if name=="비트코인":return f"{value:,.0f}"
 return f"{value:,.2f}"
def main():
 market=[];q={}
 for name,sym in MARKET.items():
  try:val,chg=latest_extended(sym)
  except Exception:val,chg=None,0
  q[name]=(val,chg)
  if name!="VIX":market.append({"name":name,"value":fmt_market(name,val),"change_pct":round(chg or 0,2)})
 try:
  un=fred_series("UNRATE");ur=float(un.iloc[-1].value);ur3=float(un.iloc[-4].value);emp_state="냉각" if ur-ur3>=.2 else "강함";emp_summary=f"실업률 {ur:.1f}% · 3개월 {ur-ur3:+.1f}%p"
 except Exception:emp_state,emp_summary="중립","UNRATE 연결 오류"
 try:
  cp=fred_series("CPIAUCSL");vals=cp["value"];y=(vals.iloc[-1]/vals.iloc[-13]-1)*100;yp=(vals.iloc[-2]/vals.iloc[-14]-1)*100;inf_state="냉각" if y<yp else "부담";inf_summary=f"CPI YoY {y:.2f}% · 이전 {yp:.2f}%"
 except Exception:inf_state,inf_summary="중립","CPI 연결 오류"
 ten=q.get("미 10년물",(None,0));ten_chg=ten[1] or 0;rate_state="부담" if ten_chg>.5 else "중립";rate_summary=f"10년물 {fmt_market('미 10년물',ten[0])} · 당일 {ten_chg:+.2f}%"
 try:
  cl=fred_series("ICSA");c0=float(cl.iloc[-1].value);c4=float(cl.iloc[-5].value);econ_state="냉각" if c0>c4*1.08 else "중립";econ_summary=f"신규 실업수당 {c0/1000:.0f}K · 4주전 대비 {(c0/c4-1)*100:+.1f}%"
 except Exception:econ_state,econ_summary="중립","실업수당 연결 오류"
 dxy=q.get("DXY",(None,0))[1] or 0;liq_state="개선" if dxy<-.3 else "부담" if dxy>.3 else "중립";liq_summary=f"DXY 당일 {dxy:+.2f}%";vix=q.get("VIX",(20,0))[0] or 20;risk_state="위험회피" if vix>=25 else "주의" if vix>=20 else "강함";risk_summary=f"VIX {vix:.1f}"
 macro=[{"name":"고용","state":emp_state,"summary":emp_summary},{"name":"물가","state":inf_state,"summary":inf_summary},{"name":"금리","state":rate_state,"summary":rate_summary},{"name":"경기","state":econ_state,"summary":econ_summary},{"name":"유동성","state":liq_state,"summary":liq_summary},{"name":"위험선호","state":risk_state,"summary":risk_summary}]
 spy=daily_history(BASE);ten_signal=ten_chg/100;sectors=[]
 for name,etfs in SECTORS:
  try:calc=calc_sector(etfs[0],spy,ten_signal,vix)
  except Exception as e:calc={"status":"중립","score":50,"action":"데이터 확인","reason":str(e),"factors":{"momentum":50,"relative_strength":50,"volume":50,"macro":50}}
  sectors.append({"name":name,"etfs":etfs,**calc})
 watch=[{"symbol":s["etfs"][0],"name":s["name"],"status":s["status"],"action":s["action"]} for s in sorted(sectors,key=lambda x:x["score"],reverse=True)[:4]]
 riskoff=(vix>=20)+(rate_state=="부담")+(liq_state=="부담");slowing=(emp_state=="냉각")+(econ_state=="냉각");regime="Risk-Off / 금리·변동성 부담" if riskoff>=2 else "Late Expansion / 둔화 관찰" if slowing>=1 else "Risk-On / 확장"
 out={"updated_at_kst":datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M"),"market":market,"regime":{"name":regime,"reasons":[emp_summary,rate_summary,risk_summary]},"macro":macro,"sectors":sectors,"watchlist":watch,"events":[{"name":"CPI","when":"발표일 자동화는 v2"},{"name":"NFP","when":"발표일 자동화는 v2"},{"name":"실업률","when":"NFP와 동시"},{"name":"소매판매","when":"발표일 자동화는 v2"},{"name":"FOMC","when":"일정 자동화는 v2"},{"name":"실업수당","when":"매주 목요일"}]};OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8");print(f"Wrote {OUT}")
if __name__=="__main__":main()
