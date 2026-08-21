from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, time
from io import StringIO
from urllib.parse import quote
from zoneinfo import ZoneInfo
import pandas as pd
import requests
import yfinance as yf

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'dashboard.json'
MARKET={'나스닥 선물':'NQ=F','S&P500 선물':'ES=F','SOX':'^SOX','WTI 원유':'CL=F','미 10년물':'^TNX','DXY':'DX-Y.NYB','비트코인':'BTC-USD','달러/원':'KRW=X','VIX':'^VIX'}
SECTORS=[('반도체/AI 하드웨어',['SMH','SOXX']),('바이오/헬스케어',['XBI','IBB']),('산업재',['XLI']),('소재/원자재',['XLB']),('에너지',['XLE']),('금융',['XLF']),('금/금광',['GLD','GDX']),('빅테크',['QQQM']),('필수소비재',['XLP']),('리츠',['VNQ'])]
BASE='SPY'

def yahoo_url(symbol): return f"https://finance.yahoo.com/quote/{quote(symbol,safe='')}/"
def fred_url(sid): return f"https://fred.stlouisfed.org/series/{sid}"
def investing_search(q): return f"https://www.investing.com/search/?q={quote(q,safe='')}"
def bloomberg_search(q): return f"https://www.bloomberg.com/search?query={quote(q,safe='')}"
def clamp(x,lo=0,hi=100): return max(lo,min(hi,x))
def daily_history(symbol,period='6mo'):
    d=yf.Ticker(symbol).history(period=period,interval='1d',auto_adjust=True)
    if d is None or d.empty: raise RuntimeError(f'No daily data for {symbol}')
    return d
def latest_extended(symbol):
    t=yf.Ticker(symbol);intr=t.history(period='1d',interval='5m',prepost=True,auto_adjust=True);daily=t.history(period='5d',interval='1d',auto_adjust=True)
    if daily.empty:return None,None,0.0
    prev=float(daily['Close'].iloc[-2] if len(daily)>=2 else daily['Close'].iloc[-1]);latest=float(intr['Close'].dropna().iloc[-1]) if intr is not None and not intr.empty and not intr['Close'].dropna().empty else float(daily['Close'].iloc[-1]);chg=(latest/prev-1)*100 if prev else 0.0
    return latest,prev,chg
def fred_series(sid):
    r=requests.get(f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}',timeout=30);r.raise_for_status();d=pd.read_csv(StringIO(r.text));d.columns=['date','value'];d['value']=pd.to_numeric(d['value'],errors='coerce');return d.dropna()
def market_session(now_et):
    if now_et.weekday()>=5:return '주말/휴장','미국장 휴장 시점의 마지막 데이터 관점'
    t=now_et.time()
    if time(4)<=t<time(9,30):return '프리마켓','미국 정규장 개장 전(프리마켓) 관점'
    if time(9,30)<=t<time(16):return '정규장','미국 정규장 진행 중 관점'
    if time(16)<=t<time(20):return '애프터마켓','미국 정규장 마감 후(애프터마켓) 관점'
    return '장 외 시간','미국장 외 시간의 마지막 데이터 관점'
def tnx_yield(x):
    if x is None:return None
    x=float(x)
    return x/10.0 if x>20 else x

def market_sources(name,sym):
    official={
      'NQ=F':('CME Group','E-mini Nasdaq-100 선물 공식 시장','https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.html'),
      'ES=F':('CME Group','E-mini S&P 500 선물 공식 시장','https://www.cmegroup.com/markets/equities/sp/e-mini-sandp500.html'),
      '^SOX':('Nasdaq','PHLX Semiconductor Sector Index','https://www.nasdaq.com/market-activity/index/sox'),
      'CL=F':('CME Group','WTI Crude Oil 선물 공식 시장','https://www.cmegroup.com/markets/energy/crude-oil/light-sweet-crude.html'),
      '^TNX':('FRED','미국 10년 국채금리 DGS10','https://fred.stlouisfed.org/series/DGS10'),
      'DX-Y.NYB':('ICE','U.S. Dollar Index 공식 시장','https://www.ice.com/products/194/US-Dollar-Index-Futures'),
      'BTC-USD':('Coinbase','Bitcoin 가격','https://www.coinbase.com/price/bitcoin'),
      'KRW=X':('한국은행 ECOS','공식 경제통계 시스템','https://ecos.bok.or.kr/')
    }
    a=[]
    if sym in official:
        l,n,u=official[sym];a.append({'label':l,'note':n,'url':u})
    a.append({'label':'Yahoo Finance','note':'대시보드 무료 시세 수집 계열','url':yahoo_url(sym)})
    a.append({'label':'Investing.com','note':'대체 시세·차트 검색','url':investing_search(name)})
    return a

def etf_sources(sym):
    return [
      {'label':'Yahoo Finance','note':'무료 시세·프리마켓 확인','url':yahoo_url(sym)},
      {'label':'Nasdaq','note':'ETF 시세·정보','url':f'https://www.nasdaq.com/market-activity/etf/{sym.lower()}'},
      {'label':'Investing.com','note':'시세·차트 검색','url':investing_search(sym)},
      {'label':'Bloomberg','note':'시세·뉴스 검색','url':bloomberg_search(sym)}]

def calc_sector(primary,spy_df,spy_ext_price=None,ten_y_change=0,vix=20):
    d=daily_history(primary);c=d['Close'].dropna();v=d['Volume'].fillna(0);ext,_,extchg=latest_extended(primary);px=ext if ext else float(c.iloc[-1]);r5=(px/c.iloc[-5]-1)*100 if len(c)>=5 else 0;r20=(px/c.iloc[-20]-1)*100 if len(c)>=20 else 0;sp=spy_df['Close'].dropna();spy_px=spy_ext_price if spy_ext_price else float(sp.iloc[-1]);sp20=(spy_px/sp.iloc[-20]-1)*100 if len(sp)>=20 else 0;rs=r20-sp20;v20=v.tail(20).mean() if len(v)>=20 else 0;vr=(v.tail(5).mean()/v20) if v20 else 1
    mom=clamp(50+r5*3+r20*1.3);rel=clamp(50+rs*5);vol=clamp(50+(vr-1)*70);macro=55;rate_sensitive=primary in {'QQQM','SMH','SOXX','XBI','IBB','VNQ'};defensive=primary in {'XLP','GLD','GDX'};cyclical=primary in {'XLI','XLB','XLE','XLF'}
    if rate_sensitive:macro+=-10 if ten_y_change>0.05 else 7 if ten_y_change<-0.05 else 0
    if defensive and vix>22:macro+=10
    if cyclical and vix<22:macro+=5
    macro=clamp(macro);total=round(mom*.35+rel*.30+vol*.20+macro*.15);status='상승 사이클' if total>=75 else '초기 관심' if total>=60 else '중립' if total>=45 else '약화';action='추격주의 · 조정 대기' if status=='상승 사이클' and r5>5 else '분할 접근' if status=='상승 사이클' else '소액 진입 검토' if status=='초기 관심' and r5<3 else '관찰 · 조정 대기' if status=='초기 관심' else '추가매수 보류' if status=='약화' else '관찰';reason=f'프리/정규 최신가 반영 · 당일 {extchg:+.1f}% · 5일 {r5:+.1f}% · 20일 {r20:+.1f}% · SPY 대비 20일 {rs:+.1f}%p · 5/20일 거래량 {vr:.2f}배'
    return {'status':status,'score':int(total),'action':action,'reason':reason,'factors':{'momentum':round(mom),'relative_strength':round(rel),'volume':round(vol),'macro':round(macro)}}

def format_market(name,latest,prev,chg):
    if latest is None:return '-','-',0.0
    if name=='미 10년물':
        y=tnx_yield(latest);p=tnx_yield(prev) if prev is not None else y;bp=(y-p)*100;return f'{y:.3f}%',f'{bp:+.1f}bp',bp
    value=f'{latest:,.2f}' if name in {'WTI 원유','DXY'} else f'{latest:,.1f}' if name=='달러/원' else f'{latest:,.0f}' if name=='비트코인' else f'{latest:,.2f}'
    return value,f'{chg:+.2f}%',chg

def investment_environment(macro):
    score_map={'우호':80,'중립':55,'주의':35,'위험':15}
    vals=[score_map.get(x.get('investment_grade','중립'),55) for x in macro];score=round(sum(vals)/len(vals)) if vals else 50
    if score>=70:label='🟢 우호';guidance='위험자산에 비교적 우호적입니다. 강한 섹터를 중심으로 분할 접근하되 급등 추격은 구분하세요.'
    elif score>=55:label='🔵 중립~우호';guidance='선별 매수 환경입니다. 지수 전체보다 상대강도가 개선되는 섹터를 우선 관찰하세요.'
    elif score>=40:label='🟡 중립~주의';guidance='공격적 추격매수에는 불리합니다. 분할 접근과 현금 여력을 유지하고 금리민감 고밸류 자산은 선별하세요.'
    else:label='🔴 위험';guidance='위험회피 환경입니다. 신규 고베타 비중 확대보다 방어와 현금 관리가 우선인 구간입니다.'
    return {'score':score,'label':label,'guidance':guidance}

def main():
    now_utc=datetime.now(ZoneInfo('UTC'));now_kst=now_utc.astimezone(ZoneInfo('Asia/Seoul'));now_et=now_utc.astimezone(ZoneInfo('America/New_York'));session,perspective=market_session(now_et);market=[];quotes={}
    for name,sym in MARKET.items():
        try:latest,prev,chg=latest_extended(sym)
        except Exception:latest,prev,chg=None,None,0.0
        quotes[name]=(latest,prev,chg)
        if name!='VIX':
            value,change_text,direction=format_market(name,latest,prev,chg);market.append({'name':name,'symbol':sym,'value':value,'change_pct':round(chg or 0,2),'change_text':change_text,'change_direction':round(direction or 0,4),'source_label':'Yahoo Finance','source_url':yahoo_url(sym),'sources':market_sources(name,sym)})
    try:
        un=fred_series('UNRATE');ur=float(un.iloc[-1].value);ur3=float(un.iloc[-4].value);du=ur-ur3;emp_state='냉각' if du>=.2 else '강함';emp_summary=f'실업률 {ur:.1f}% · 3개월 {du:+.1f}%p';emp_grade='주의' if du>=.3 else '중립';emp_hint='고용 둔화가 빨라지면 경기민감주에는 부담. 견조한 고용은 경기에 긍정적이나 금리인하 기대를 늦출 수 있음.'
    except Exception:emp_state,emp_summary,emp_grade,emp_hint='중립','UNRATE 연결 오류','중립','고용 데이터 확인 필요'
    try:
        cp=fred_series('CPIAUCSL');vals=cp['value'];y=(vals.iloc[-1]/vals.iloc[-13]-1)*100;yp=(vals.iloc[-2]/vals.iloc[-14]-1)*100;inf_state='냉각' if y<yp else '부담';inf_summary=f'CPI YoY {y:.2f}% · 이전 {yp:.2f}%';inf_grade='우호' if y<yp else '주의';inf_hint='물가 둔화는 장기금리 안정에 도움 → 성장주·바이오·리츠에 상대적으로 우호.' if inf_grade=='우호' else '물가 재가속은 금리 상승 압력 → 고밸류 성장주·리츠에 부담.'
    except Exception:inf_state,inf_summary,inf_grade,inf_hint='중립','CPI 연결 오류','중립','물가 데이터 확인 필요'
    ten_latest,ten_prev,_=quotes.get('미 10년물',(None,None,0));ten_y=tnx_yield(ten_latest);ten_prev_y=tnx_yield(ten_prev) if ten_prev is not None else ten_y;ten_bp=(ten_y-ten_prev_y)*100 if ten_y is not None else 0;rate_state='부담' if (ten_y and ten_y>=4.5) or ten_bp>=5 else '중립';rate_summary=f'10년물 {ten_y:.3f}% · 당일 {ten_bp:+.1f}bp' if ten_y is not None else '10년물 연결 오류';rate_grade='주의' if rate_state=='부담' else '중립';rate_hint='장기금리가 높거나 상승하면 QQQ·반도체·바이오·리츠 같은 금리민감 자산에 부담.' if rate_grade=='주의' else '금리 부담이 급격히 확대되지 않는 중립 구간.'
    try:
        cl=fred_series('ICSA');c0=float(cl.iloc[-1].value);c4=float(cl.iloc[-5].value);ratio=c0/c4-1;econ_state='냉각' if ratio>.08 else '중립';econ_summary=f'신규 실업수당 {c0/1000:.0f}K · 4주전 대비 {ratio*100:+.1f}%';econ_grade='주의' if ratio>.12 else '중립';econ_hint='실업수당 급증은 경기민감·소비 관련 자산에 부담. 현재는 단일 지표라 보조 신호로만 사용.'
    except Exception:econ_state,econ_summary,econ_grade,econ_hint='중립','실업수당 연결 오류','중립','경기 데이터 확인 필요'
    dxy=quotes.get('DXY',(None,None,0))[2] or 0;liq_state='개선' if dxy<-.3 else '부담' if dxy>.3 else '중립';liq_summary=f'DXY 당일 {dxy:+.2f}%';liq_grade='우호' if liq_state=='개선' else '주의' if liq_state=='부담' else '중립';liq_hint='달러 약세는 글로벌 위험자산·원자재 유동성에 상대적으로 우호.' if liq_grade=='우호' else '달러 강세는 글로벌 유동성과 원자재·신흥 위험자산에 부담.' if liq_grade=='주의' else '달러 흐름이 중립 범위.'
    vix=quotes.get('VIX',(20,None,0))[0] or 20;risk_state='위험회피' if vix>=25 else '주의' if vix>=20 else '강함';risk_summary=f'VIX {vix:.1f}';risk_grade='위험' if vix>=30 else '주의' if vix>=25 else '중립' if vix>=18 else '우호';risk_hint='VIX가 낮을수록 위험선호에 우호. 급등 시 고베타·레버리지 자산 변동성 확대에 주의.'
    macro=[
      {'name':'고용','state':emp_state,'summary':emp_summary,'investment_grade':emp_grade,'investment_hint':emp_hint,'sources':[{'label':'FRED · UNRATE','note':'미국 실업률 공식 시계열','url':fred_url('UNRATE')},{'label':'BLS','note':'미 노동통계국 고용 원문','url':'https://www.bls.gov/cps/'}]},
      {'name':'물가','state':inf_state,'summary':inf_summary,'investment_grade':inf_grade,'investment_hint':inf_hint,'sources':[{'label':'FRED · CPIAUCSL','note':'CPI 공식 시계열','url':fred_url('CPIAUCSL')},{'label':'BLS CPI','note':'CPI 원문','url':'https://www.bls.gov/cpi/'}]},
      {'name':'금리','state':rate_state,'summary':rate_summary,'investment_grade':rate_grade,'investment_hint':rate_hint,'sources':[{'label':'FRED · DGS10','note':'미국 10년 국채금리','url':fred_url('DGS10')},{'label':'U.S. Treasury','note':'미 재무부 금리 자료','url':'https://home.treasury.gov/resource-center/data-chart-center/interest-rates'}]},
      {'name':'경기','state':econ_state,'summary':econ_summary,'investment_grade':econ_grade,'investment_hint':econ_hint,'sources':[{'label':'FRED · ICSA','note':'신규 실업수당','url':fred_url('ICSA')},{'label':'U.S. DOL','note':'실업보험 통계','url':'https://www.dol.gov/ui/data.pdf'}]},
      {'name':'유동성','state':liq_state,'summary':liq_summary,'investment_grade':liq_grade,'investment_hint':liq_hint,'sources':[{'label':'ICE','note':'U.S. Dollar Index 공식 시장','url':'https://www.ice.com/products/194/US-Dollar-Index-Futures'},{'label':'Yahoo Finance','note':'DXY 시세','url':yahoo_url('DX-Y.NYB')}]},
      {'name':'위험선호','state':risk_state,'summary':risk_summary,'investment_grade':risk_grade,'investment_hint':risk_hint,'sources':[{'label':'Cboe VIX','note':'VIX 공식 자료','url':'https://www.cboe.com/tradable_products/vix/'},{'label':'Yahoo Finance','note':'VIX 시세','url':yahoo_url('^VIX')}]}
    ]
    env=investment_environment(macro)
    spy=daily_history(BASE);spy_ext,_,_=latest_extended(BASE);ten_signal=ten_bp/100;sectors=[]
    for name,etfs in SECTORS:
        try:calc=calc_sector(etfs[0],spy,spy_ext_price=spy_ext,ten_y_change=ten_signal,vix=vix)
        except Exception as e:calc={'status':'중립','score':50,'action':'데이터 확인','reason':str(e),'factors':{'momentum':50,'relative_strength':50,'volume':50,'macro':50}}
        sectors.append({'name':name,'etfs':etfs,**calc})
    watch=[]
    for s in sorted(sectors,key=lambda x:x['score'],reverse=True)[:4]:
        sym=s['etfs'][0]
        try:latest,prev,chg=latest_extended(sym)
        except Exception:latest,prev,chg=None,None,0.0
        watch.append({'symbol':sym,'name':s['name'],'status':s['status'],'action':s['action'],'as_of_kst':now_kst.strftime('%Y-%m-%d %H:%M'),'as_of_et':now_et.strftime('%Y-%m-%d %H:%M'),'sources':etf_sources(sym)})
    slowing=(emp_state=='냉각')+(econ_state=='냉각');phase='둔화 관찰' if slowing>=1 else '확장/중립';reasons=[f'고용: {emp_summary} → 투자환경 {emp_grade}',f'금리: {rate_summary} → 투자환경 {rate_grade}',f'위험선호: {risk_summary} → 투자환경 {risk_grade}']
    out={'updated_at_kst':now_kst.strftime('%Y-%m-%d %H:%M'),'meta':{'updated_at_iso':now_kst.isoformat(timespec='seconds'),'updated_at_utc_iso':now_utc.isoformat(timespec='seconds'),'updated_at_kst':now_kst.strftime('%Y-%m-%d %H:%M'),'updated_at_et':now_et.strftime('%Y-%m-%d %H:%M'),'market_session':session,'perspective':perspective,'update_policy':'서버: 평일 20:00~23:30 KST 30분 간격 · 화면: 5분마다 새 데이터 자동 확인 · GitHub Actions는 지연 가능','cautions':['수집 시점과 미국장 세션을 먼저 확인하세요.','Yahoo Finance extended-hours는 무료 비공식 데이터로 지연·누락이 있을 수 있습니다.','투자환경 등급과 산업 점수는 미래 수익률 확률이 아닌 규칙 기반 보조 신호입니다.','FRED 거시지표는 각 지표의 최신 공식 발표값이며 장중 실시간 수치가 아닙니다.','외부 원자료·시세 사이트와 대시보드의 수집 시점이 달라 값이 다를 수 있습니다.','현재 버전은 뉴스·기업 실적·가이던스의 의미를 자동 점수에 완전히 반영하지 않습니다.']},'market':market,'regime':{'name':phase,'environment':env,'reasons':reasons},'macro':macro,'sectors':sectors,'watchlist':watch,'events':[{'name':'CPI','when':'발표 일정 자동화는 다음 단계'},{'name':'NFP','when':'발표 일정 자동화는 다음 단계'},{'name':'실업률','when':'NFP와 동시 발표'},{'name':'소매판매','when':'발표 일정 자동화는 다음 단계'},{'name':'FOMC','when':'일정 자동화는 다음 단계'},{'name':'실업수당','when':'매주 목요일'}]}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(f'Wrote {OUT}');print(f'Perspective: {perspective}')
if __name__=='__main__':main()
