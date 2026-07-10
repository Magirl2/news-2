# 텔레그램 실시간 투자 보조 봇

기존 카카오 아침 보고서 봇은 그대로 두고, 텔레그램에서 종목 조회와 가격 조건 알림을 쓰기 위한 별도 기능입니다.

## 할 수 있는 일

- `AMD`처럼 종목명을 보내면 최신 가격/차트 기준으로 분석합니다.
- `AMD 155 돌파 알림`처럼 가격 조건 알림을 등록합니다.
- `알림 목록`, `알림 삭제 AMD`, `알림 전체 삭제`를 지원합니다.
- 기존 아침 보고서의 `reports/signals/latest.json`이 있으면 아침 판단과 현재 판단을 비교합니다.

## 중요한 주의

- 단정적인 주문 지시나 전액 진입처럼 보이는 표현은 쓰지 않습니다.
- Yahoo Finance 무료 데이터는 지연될 수 있습니다.
- 실제 주문 전 호가, 거래량, 장중 뉴스는 직접 확인해야 합니다.
- 무료 Render/Railway 서버는 잠들 수 있어 장중 알림이 늦을 수 있습니다.

## BotFather로 봇 만들기

1. 텔레그램에서 `@BotFather`를 검색합니다.
2. `/newbot`을 보냅니다.
3. 봇 이름을 정합니다.
4. 봇 username을 정합니다. 보통 `_bot`으로 끝나야 합니다.
5. BotFather가 주는 token을 복사합니다.
6. 이 token을 `TELEGRAM_BOT_TOKEN` 환경변수에 넣습니다.

## 내 chat_id 확인

가장 쉬운 방법:

1. 만든 봇에게 `/start`를 보냅니다.
2. 브라우저에서 아래 주소를 엽니다.

```text
https://api.telegram.org/bot여기에_봇토큰/getUpdates
```

3. 화면에서 `"chat":{"id":...}` 숫자를 찾습니다.
4. 그 숫자를 `TELEGRAM_ALLOWED_CHAT_IDS`에 넣습니다.

## 로컬 테스트

종목 분석:

```powershell
py -m market_briefing_bot.live_bot analyze AMD
```

명령 파싱:

```powershell
py -m market_briefing_bot.live_bot parse "AMD 155 돌파 알림"
```

텔레그램 봇 실행:

```powershell
py -m market_briefing_bot.live_bot run-telegram
```

## 환경변수

필수:

```text
TELEGRAM_BOT_TOKEN=
```

권장:

```text
TELEGRAM_ALLOWED_CHAT_IDS=
LIVE_BOT_DB_PATH=data/live_bot.sqlite
LIVE_CHECK_INTERVAL_SECONDS=60
REPORTS_DIR=reports
```

## Render 배포 예시

Build command:

```text
pip install -r requirements.txt
```

Start command:

```text
python -m market_briefing_bot.live_bot run-telegram
```

Environment variables:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_ALLOWED_CHAT_IDS
LIVE_BOT_DB_PATH
LIVE_CHECK_INTERVAL_SECONDS
```

## 사용 예

```text
AMD
NVDA 지금 어때
TSLA 손익비
AMD 155 돌파 알림
NVDA 150 이탈 알림
AMD 추가진입 알림
AMD 무효화 알림
알림 목록
알림 삭제 AMD
알림 전체 삭제
```

## 메시지 의미

진입 가능 여부:

- 지금 소량 가능
- 지금은 1차 진입만 가능
- 눌림 확인 후 가능
- 돌파 확인 후 가능
- 추격 금지
- 제외

비중 판단:

- 공격 비중 가능
- 손익비 우수
- 비중 확대 가능
- 작게만 가능
- 진입 부적합

이 표현들은 투자 조언이 아니라 리스크 관리용 참고 신호입니다.
