# 미국 산업 사이클 레이더 v1

무료 데이터 + GitHub Pages + GitHub Actions로 동작하는 개인용 시장 대시보드입니다.

## 바로 쓰는 순서
1. GitHub에서 새 repository 생성
2. 이 폴더의 모든 파일을 repository 루트에 업로드
3. Settings → Pages → Deploy from a branch → main / (root)
4. Actions 탭에서 `Update market data`를 한 번 수동 실행
5. 생성된 Pages 주소 확인

## 자동 갱신
평일 13:00 UTC = 22:00 KST. GitHub Actions 스케줄은 몇 분 이상 늦게 시작될 수 있습니다.

## 데이터
- 거시: FRED 공식 CSV
- 시장/프리마켓: yfinance/Yahoo Finance 비공식 extended-hours 데이터

## 중요한 제한
- 프리마켓 데이터는 무료·비공식 소스라 지연/누락/변경 가능성이 있습니다.
- GitHub Pages 무료 정적 사이트에는 로그인/접근제어가 없습니다. URL을 개인적으로 쓰는 형태입니다.
- v1 점수에는 뉴스/기업 가이던스를 넣지 않았습니다. 없는 데이터를 임의 점수화하지 않기 위해 제외했습니다.

## v1 점수
- 가격 모멘텀 35%
- SPY 대비 상대강도 30%
- 거래량/수급 20%
- Macro 환경 15%

판정: 75+ 상승 사이클 / 60~74 초기 관심 / 45~59 중립 / 44 이하 약화

## v2 후보
- 경제지표 발표 캘린더 자동화
- 기업 IR/공식 RSS 촉매 감지
- 섹터별 상대강도 히스토리 저장
- 중립→초기 관심 전환 알림
- 사용자 보유종목 연결
