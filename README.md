# 미국 산업 사이클 레이더 v1

## Telegram 교집합 알림

기존 `Update market data` 수집 과정에서 `data/dashboard.json`의 아래 세 조건을 함께 판정합니다.

- `timing.fear_greed.score <= 9`
- `timing.metrics.vix >= 28`
- `timing.metrics.spy_rsi14 <= 30`

2/3은 웹 관심 신호만 표시하고, 3/3 진입 때만 Telegram으로 발송합니다. 기존 산업별 진입·종료·품질 알림과 별도 테스트 메시지 경로는 제거했습니다. 같은 신호가 유지되면 재발송하지 않으며, 최신의 유효한 데이터에서 신호 해제를 확인한 뒤 다시 3/3이 되면 새로 발송합니다. 최초 실행 시에도 유효한 3/3이면 한 번 발송합니다.

### 설정

1. Telegram에서 봇을 만들고 봇과 대화를 시작합니다. 그룹이면 봇을 추가하고 메시지 전송 권한을 확인합니다.
2. 저장소 Settings → Secrets and variables → Actions → Repository secrets에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`를 등록합니다. 값은 HTML, JavaScript, 공개 저장소, 이슈, 로그에 넣지 않습니다.
3. `main`에 변경사항을 적용하고 Actions → Update market data → Run workflow를 실행합니다. 기존 수집 스케줄은 유지됩니다. `main` 외 브랜치에서 수동 실행하면 작업을 건너뜁니다.
4. `Evaluate intersection and reserve notification` 단계의 `valid`, `count`, `dispatch`를 확인합니다. Secrets가 없으면 수집과 웹 갱신은 계속하며 경고를 남기고 발송하지 않습니다. 조건 미충족 때 메시지가 없는 것이 정상입니다.

### 데이터 유효성

수집 시각 `meta.updated_at_utc_iso`는 2시간 이내, 각 지표의 원자료 시각은 36시간 이내여야 합니다. 5분보다 먼 미래 시각, 시각 누락, 시간대 없는 시각, NaN/Infinity, 문자열 숫자, 범위 밖 값과 `fear_greed.available != true`는 발송을 차단합니다. 날짜만 있는 CNN 미러 자료도 정확한 원자료 시각을 확인할 수 없어 발송하지 않습니다.

VIX는 실제 선택된 시세 행의 시각을 `timing.source_as_of.vix`에 저장합니다. RSI는 계산에 사용한 SPY 일봉과 선택된 최신 시세 중 오래된 시각을 `timing.source_as_of.spy_rsi14`에 저장합니다. 기존 VIX 표시용 기본값 20은 알림 판정에 사용하지 않습니다. 이 보수적인 36시간 기준은 주말·휴장일에도 완화하지 않아, 새 데이터가 들어올 때까지 신호를 보류할 수 있습니다.

결측 또는 오래된 자료는 발송뿐 아니라 신호 해제 판정도 막습니다. 웹은 같은 시간·값 기준으로 표시하고 1분마다 유효기간을 다시 확인합니다. 웹의 “Telegram 알림 대상”은 조건 충족 표시이며 실제 배달 확인을 뜻하지 않습니다.

### 중복 방지와 실패 처리

`data/telegram_state.json`은 워크플로우가 최초 실행 시 생성합니다. 민감 정보 없이 활성 여부와 발송 상태만 저장하며, 다음 실행이 이어받도록 `main`에 커밋합니다. 파일을 삭제하거나 과거 상태로 되돌리면 중복 방지 기록이 사라지므로 보존해야 합니다. 기존 `data/history.json`의 산업 알림 기록과는 별도입니다.

워크플로우 순서는 수집 → JSON 검사 → 교집합 판정·발송 예약 → 데이터와 예약 상태 커밋·push → Telegram 전송 → 성공 기록 커밋입니다. 동일 concurrency 그룹으로 실행을 직렬화하고, 예약 push 실패 시 전송 단계로 가지 않습니다.

Telegram API에는 이 코드가 사용할 메시지 중복 제거 키가 없어 네트워크 단절 시 “전송됐으나 응답만 유실”과 “미전송”을 구분할 수 없습니다. 중복 금지를 우선해 예약 이후 실패·취소·타임아웃은 자동 재시도하지 않습니다. 따라서 예약 직후 실행이 중단되면 해당 신호 알림이 누락될 수 있습니다. `delivery: reserved`는 배달 성공을 뜻하지 않습니다. 다음의 유효한 해제와 재진입은 정상적으로 새 알림을 만듭니다.

수동 복구가 필요하면 해당 Actions 실행이 종료됐는지, 실제 Telegram에 메시지가 도착했는지 먼저 확인합니다. 미전송이 확실한 경우에만 관리자가 상태 파일을 `{"version":1,"active":false}`로 수정해 커밋한 뒤 다시 실행할 수 있습니다. 전송 여부가 불명확하면 예약을 유지합니다. 발송 상태 파일 자체가 손상되면 작업을 실패 처리하고 자동 초기화하지 않습니다.

### 검증

```sh
python -m unittest discover -s tests -v
node tests/test_web_intersection.cjs
```

Python 테스트는 임계값 경계·8가지 조합, 각 지표 결측·오래된 시각, 상태 유지·해제·재진입, 별도 실행을 위한 상태 저장, Secrets 누락, 모의 Telegram 성공·실패, 오류 메시지의 토큰 비노출과 워크플로우 순서를 검사합니다. 웹 테스트는 0~3/3 표시와 결측·유효기간을 검사합니다. 모의 전송은 실제 봇에 메시지를 보내지 않습니다.

ChatGPT의 시간별 자동화와 GitHub Actions 발송은 서로 별개입니다.

무료 데이터 + GitHub Pages + GitHub Actions로 동작하는 개인용 시장 대시보드입니다.
https://real4goon.github.io/cycle-radar/

## 바로 쓰는 순서
1. GitHub에서 새 repository 생성
2. 이 폴더의 모든 파일을 repository 루트에 업로드
3. Settings → Pages → Deploy from a branch → main / (root)
4. Actions 탭에서 `Update market data`를 한 번 수동 실행
5. 생성된 Pages 주소 확인

## 자동 갱신
월~금 UTC 08:00~23:30에는 30분 간격으로, 화~토 UTC 00:00에는 최종 갱신합니다. 한국 시각으로 월~금 17:00~익일 08:30의 30분 간격 갱신과 화~토 09:00 최종 갱신입니다. GitHub Actions 스케줄은 지연될 수 있습니다.

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
