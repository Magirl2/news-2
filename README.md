# 미국장 마감 카카오톡 브리핑 봇

매일 미국장 종가 이후에 시장 요약, 섹터 흐름, 주요 투자 뉴스를 한국어로 정리해 카카오톡 `나와의 채팅`으로 보내는 봇입니다.

처음 설정만 빠르게 하고 싶으면 `START_HERE.md`부터 보세요.

이 첫 버전은 최대한 쉽게 쓰도록 만들었습니다.

- Python 기본 기능만 사용합니다.
- API 키 같은 비밀값은 `.env`와 `.secrets`에 따로 보관합니다.
- 시장 데이터는 Yahoo Finance 차트 데이터를 사용합니다.
- 뉴스는 공식 RSS 피드를 읽습니다.
- 카카오톡은 공식 Kakao Developers의 `나에게 보내기` 방식을 사용합니다.

## 1. 처음 한 번만 준비하기

### Python 확인

터미널에서 아래 명령을 실행합니다.

```powershell
py --version
```

Python 3.10 이상이면 좋습니다. 이 컴퓨터에서는 `py` 명령이 정상 동작합니다.

### 설정 파일 만들기

`.env.example` 파일을 복사해서 `.env`라는 이름으로 만듭니다.

```powershell
Copy-Item .env.example .env
```

## 2. 카카오 설정하기

처음에는 `나에게 보내기` 방식이 가장 쉽습니다. 본인 카카오톡의 `나와의 채팅`으로 메시지가 옵니다.

더 자세한 그림 없는 단계별 안내는 `docs\KAKAO_SETUP.md`에 정리해 두었습니다.

1. Kakao Developers에 접속합니다.  
   https://developers.kakao.com/
2. 내 애플리케이션을 하나 만듭니다.
3. 앱의 `REST API 키`를 복사합니다.
4. 아래 명령을 실행하고, 복사한 키를 붙여넣습니다.

```powershell
py -m market_briefing_bot configure-kakao
```

직접 편집하고 싶다면 `.env` 파일의 `KAKAO_REST_API_KEY=` 뒤에 붙여넣어도 됩니다.
5. 카카오 로그인 설정에서 Redirect URI를 추가합니다.

```text
http://localhost:8765/callback
```

6. 동의 항목에서 카카오톡 메시지 전송 권한이 있으면 사용 설정합니다. 이름은 보통 `talk_message` 또는 `카카오톡 메시지 전송`으로 표시됩니다.

그 다음 아래 명령을 실행합니다.

```powershell
py -m market_briefing_bot kakao-login
```

화면에 긴 주소가 나오면 브라우저에 붙여넣고 카카오 로그인을 승인합니다. 성공하면 `.secrets/kakao_tokens.json` 파일이 생깁니다.

## 3. 먼저 보고서만 미리보기

카카오톡으로 보내기 전에 보고서가 잘 만들어지는지 확인합니다.

```powershell
py -m market_briefing_bot preview
```

성공하면 화면에 브리핑이 나오고 `reports` 폴더에도 저장됩니다.

같은 폴더에 `.html` 보고서도 함께 만들어집니다. 이 파일은 브라우저에서 열어 전체 섹터 흐름을 카드 형태로 볼 수 있습니다.

## 4. 카카오톡 테스트 메시지 보내기

```powershell
py -m market_briefing_bot send-test
```

카카오톡 `나와의 채팅`에 테스트 메시지가 오면 연결이 끝난 것입니다.

## 5. 실제 브리핑 보내기

```powershell
py -m market_briefing_bot send
```

카카오톡 메시지는 글자 수 제한 때문에 여러 개로 나뉘어 올 수 있습니다.

섹터맵의 `++`, `+`, `0`, `-`, `--` 표시는 강도를 뜻합니다. `++`는 강한 상승, `--`는 강한 하락입니다.

## 6. 매일 자동 실행하기

Windows 작업 스케줄러에 매일 오전 7시 10분 실행 작업을 등록합니다. 미국장이 서머타임이어도 오전 7시 10분이면 장 마감 이후라 안전합니다.

```powershell
.\scripts\create_windows_task.ps1
```

등록됐는지 확인하려면 아래 명령을 실행합니다.

```powershell
.\scripts\check_windows_task.ps1
```

자동 실행을 끄고 싶으면 아래 명령을 실행합니다.

```powershell
.\scripts\remove_windows_task.ps1
```

등록 후에는 매일 아침 자동으로 `send`가 실행됩니다. 컴퓨터가 꺼져 있으면 실행되지 않으니, 완전 자동화를 원하면 나중에 클라우드 실행으로 옮기는 것이 좋습니다.

## 내 컴퓨터 실행 vs 클라우드 실행

처음 추천은 `내 컴퓨터 실행`입니다.

- 장점: 무료, 설정이 단순함, 바로 테스트 가능
- 단점: 컴퓨터가 꺼져 있으면 실행 안 됨

나중에 추천은 `클라우드 실행`입니다.

- 장점: 컴퓨터가 꺼져 있어도 매일 실행
- 단점: GitHub Actions, 서버, 클라우드 비밀값 설정을 추가로 배워야 함

GitHub Actions로 옮기고 싶을 때는 `docs\CLOUD_SETUP.md`를 보면 됩니다. 템플릿 파일은 `.github\workflows\us-market-briefing.yml`에 준비되어 있습니다.

카카오 테스트까지 끝난 뒤 아래 명령을 실행하면 GitHub Secrets에 넣을 값을 `.secrets\github_actions_secrets.txt`에 정리해 줍니다.

```powershell
py -m market_briefing_bot prepare-cloud-secrets
```

카카오 발송 방식과 데이터 출처를 왜 이렇게 골랐는지는 `docs\DECISIONS.md`에 정리했습니다.

## 휴장일과 조기폐장

봇은 미국 동부시간을 기준으로 NYSE 주요 휴장일과 조기폐장일을 확인합니다. 휴장일이면 메시지에 휴장 안내를 넣고, 최신으로 확인 가능한 직전 거래일 데이터를 사용합니다.

## 주의

이 봇은 투자 정보를 정리해 주는 도구입니다. 매수/매도 추천이 아니며, 실제 투자 판단은 직접 확인해야 합니다.

## 앞으로 개선할 수 있는 기능

- OpenAI API를 연결해 뉴스 제목을 더 자연스러운 한국어 요약으로 바꾸기
- 실제 섹터맵 이미지를 생성하거나 첨부하기
- GitHub Actions로 클라우드 자동 실행하기
- 카카오톡 채널/알림톡으로 확장하기
- 이메일, 텔레그램, 디스코드 같은 대체 발송 방식 추가하기

## 문제가 생겼을 때

먼저 아래 명령으로 설정 상태를 확인합니다.

```powershell
py -m market_briefing_bot readiness
py -m market_briefing_bot doctor
```

`readiness`는 카카오톡 발송과 자동 실행 준비 상태를 체크리스트로 보여줍니다.

지금 바로 다음에 해야 할 일만 보고 싶으면 아래 명령을 실행합니다.

```powershell
py -m market_briefing_bot setup-next
```

실행 기록은 `logs\bot.log`에 남습니다. 자동 실행 기록은 `logs\scheduled-task.log`에 남습니다. 카카오 연결이 끝난 뒤에는 브리핑 생성 중 오류가 나면 카카오톡으로 실패 알림도 보내도록 되어 있습니다.
