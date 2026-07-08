# 선택 API 키 설정

카카오톡 발송 자체에는 아래 선택 키가 없어도 됩니다.

다만 투자 보고서를 더 실전적으로 쓰려면 아래 3개를 GitHub Secrets에 넣는 것을 권장합니다.

## 1. 관심종목

GitHub Secrets 이름:

`WATCHLIST_SYMBOLS`

값 예시:

`NVDA,AAPL,TSLA,MSFT,PLTR`

효과:

- 보유/관심종목 영향 분석
- 섹터 대비 상대강도
- 포트폴리오 섹터 쏠림
- 관심종목 SEC 공시 확인
- 관심종목 실적 캘린더

## 2. FRED 경제 이벤트 키

GitHub Secrets 이름:

`FRED_API_KEY`

발급 방법:

1. https://fred.stlouisfed.org/docs/api/api_key.html 접속
2. FRED 계정 로그인 또는 가입
3. `Request or view your API keys`에서 API 키 발급
4. 발급된 32자리 키를 GitHub Secrets의 `FRED_API_KEY`에 붙여넣기

효과:

- CPI
- PCE 물가
- 고용보고서
- FOMC
- GDP

같은 주요 경제지표 발표 일정을 보고서에 표시합니다.

## 3. Alpha Vantage 실적 캘린더 키

GitHub Secrets 이름:

`ALPHA_VANTAGE_API_KEY`

발급 방법:

1. https://www.alphavantage.co/support/#api-key 접속
2. 무료 API 키 신청
3. 받은 키를 GitHub Secrets의 `ALPHA_VANTAGE_API_KEY`에 붙여넣기

효과:

- `WATCHLIST_SYMBOLS` 종목의 다음 실적 발표일
- 실적까지 남은 날짜
- 실적 전 신규 진입 주의 여부

를 보고서에 표시합니다.

## 4. SEC User-Agent

GitHub Secrets 이름:

`SEC_USER_AGENT`

값 예시:

`your-email@example.com`

또는

`market-briefing-bot your-email@example.com`

효과:

- SEC 공시 조회 요청에 연락처를 명시합니다.
- SEC는 자동 요청 시 User-Agent를 선언하라고 안내합니다.

## GitHub에 넣는 위치

1. GitHub 저장소 `news-2`로 이동
2. `Settings`
3. 왼쪽 메뉴 `Secrets and variables`
4. `Actions`
5. `New repository secret`
6. `Name`에 위 Secret 이름 입력
7. `Secret`에 값 붙여넣기
8. `Add secret`

필수 Secret은 기존과 동일합니다.

- `KAKAO_REST_API_KEY`
- `KAKAO_TOKENS_JSON`

