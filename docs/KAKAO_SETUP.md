# 카카오톡 연결 순서

처음 한 번만 하면 됩니다.

## 1. 카카오 앱 만들기

1. https://developers.kakao.com/ 접속
2. `내 애플리케이션`으로 이동
3. 새 애플리케이션 만들기
4. 앱 이름은 예를 들어 `미국장 브리핑 봇`으로 입력

## 2. REST API 키 복사

앱 화면에서 `앱 키` 메뉴를 열고 `REST API 키`를 복사합니다.

아래 명령을 실행하고, 복사한 키를 붙여넣습니다.

```powershell
py -m market_briefing_bot configure-kakao
```

직접 편집하고 싶다면 `.env` 파일을 열어서 아래처럼 붙여넣어도 됩니다.

```text
KAKAO_REST_API_KEY=여기에_REST_API_키_붙여넣기
```

## 3. Redirect URI 등록

카카오 로그인 설정에서 Redirect URI를 아래 값으로 추가합니다.

```text
http://localhost:8765/callback
```

`.env` 파일에도 같은 값이 들어 있어야 합니다.

```text
KAKAO_REDIRECT_URI=http://localhost:8765/callback
```

## 4. 메시지 권한 확인

동의 항목 또는 권한 설정에서 `카카오톡 메시지 전송` 권한을 확인합니다. 화면에는 `talk_message`라고 표시될 수 있습니다.

## 5. 내 컴퓨터에서 로그인

아래 명령을 실행합니다.

```powershell
py -m market_briefing_bot kakao-login
```

화면에 긴 주소가 나오면 브라우저에 붙여넣습니다. 카카오 로그인과 권한 승인을 마치면 `.secrets\kakao_tokens.json` 파일이 생깁니다.

## 6. 테스트 메시지 보내기

```powershell
py -m market_briefing_bot send-test
```

카카오톡 `나와의 채팅`에 테스트 메시지가 오면 성공입니다.

## 자주 막히는 부분

- Redirect URI가 다르면 로그인이 실패합니다. 카카오 설정과 `.env`의 주소가 완전히 같아야 합니다.
- 메시지 권한을 켜지 않으면 전송이 실패할 수 있습니다.
- 카카오 메시지에는 링크가 필요합니다. 링크 도메인 관련 오류가 나오면 Kakao Developers의 제품 설정에서 `KAKAO_LINK_URL`의 도메인을 등록해 주세요.

## 오류 메시지가 나올 때

아래 명령으로 현재 설정을 먼저 확인합니다.

```powershell
py -m market_briefing_bot doctor
```

자주 나오는 원인은 보통 아래 중 하나입니다.

- `Redirect URI` 오류: Kakao Developers의 Redirect URI와 `.env`의 `KAKAO_REDIRECT_URI`가 완전히 같아야 합니다.
- `talk_message` 또는 권한 오류: 카카오톡 메시지 전송 권한을 켠 뒤 `py -m market_briefing_bot kakao-login`을 다시 실행합니다.
- 토큰 오류: `py -m market_briefing_bot kakao-login`을 다시 실행합니다.
- 링크 도메인 오류: Kakao Developers의 플랫폼/Web 사이트 도메인에 `.env`의 `KAKAO_LINK_URL` 도메인을 등록합니다. 기본값은 `finance.yahoo.com`입니다.
