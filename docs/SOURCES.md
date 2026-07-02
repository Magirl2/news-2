# 데이터와 공식 문서 출처

이 봇은 처음부터 무료 또는 저비용으로 시작하기 위해 아래 출처를 기본값으로 사용합니다.

## 카카오톡 발송

- Kakao Developers 카카오톡 메시지 REST API  
  https://developers.kakao.com/docs/latest/ko/kakaotalk-message/rest-api
- Kakao Developers 카카오 로그인 REST API  
  https://developers.kakao.com/docs/latest/ko/kakaologin/rest-api

첫 버전은 `나에게 보내기` 방식을 씁니다. 본인 카카오톡의 `나와의 채팅`으로 메시지를 보내는 방식이라 가장 단순합니다. 친구에게 보내기나 채널/알림톡은 권한, 심사, 비즈니스 설정이 더 필요합니다.

## 미국장 일정

- NYSE Trading Hours & Holidays  
  https://www.nyse.com/markets/hours-calendars

봇 코드에는 주요 NYSE 휴장일, 조기폐장일, 미국 동부시간 서머타임 처리가 들어 있습니다.

## 시장 데이터

- Yahoo Finance chart data  
  https://finance.yahoo.com/

지수와 섹터 ETF의 일별 종가를 가져옵니다. 섹터맵은 실제 지도 그림이 아니라 SPDR 섹터 ETF 등락률을 기준으로 “섹터별 강약”을 분석합니다.

처음에는 무료로 바로 작동하는 구성이 중요해서 Yahoo Finance를 사용합니다. 더 공식적인 유료/무료 API 키 방식이 필요하면 Alpha Vantage, Finnhub, Polygon 같은 데이터 API로 바꿀 수 있게 구조를 나눠 두었습니다.

## 뉴스 RSS

기본 RSS 후보:

- Yahoo Finance RSS: https://finance.yahoo.com/news/rssindex
- CNBC Markets RSS: https://www.cnbc.com/id/100003114/device/rss/rss.html
- MarketWatch Top Stories RSS: https://feeds.content.dowjones.io/public/rss/mw_topstories
- Federal Reserve RSS: https://www.federalreserve.gov/feeds/press_all.xml

RSS 제공 정책은 바뀔 수 있습니다. 특정 피드가 막히면 `.env`의 `NEWS_RSS_URLS`에 다른 공식 RSS 주소를 넣어 바꿀 수 있습니다.
