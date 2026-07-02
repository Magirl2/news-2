# 여기서부터 시작하세요

카카오톡으로 미국장 브리핑을 받기 위해 딱 한 번만 설정하면 됩니다.

## 1. 카카오 REST API 키 저장

Kakao Developers에서 REST API 키를 복사한 뒤 아래 명령을 실행합니다.

```powershell
py -m market_briefing_bot configure-kakao
```

화면이 멈춘 것처럼 보이면 복사한 키를 붙여넣고 Enter를 누르면 됩니다.

## 2. 카카오 로그인 연결

```powershell
py -m market_briefing_bot kakao-login
```

긴 주소가 나오면 브라우저에 붙여넣고 카카오 로그인을 승인합니다.

## 3. 테스트 메시지 받기

```powershell
py -m market_briefing_bot send-test
```

카카오톡 `나와의 채팅`에 테스트 메시지가 오면 성공입니다.

## 4. 실제 브리핑 보내기

```powershell
py -m market_briefing_bot send
```

## 5. 매일 자동 실행 켜기

```powershell
.\scripts\create_windows_task.ps1
```

자동 실행이 등록됐는지 확인하려면:

```powershell
.\scripts\check_windows_task.ps1
```

컴퓨터가 꺼져 있어도 실행되게 하고 싶다면 `docs\CLOUD_SETUP.md`의 GitHub Actions 방법을 나중에 사용하면 됩니다.

카카오 테스트까지 끝난 뒤 클라우드용 Secret 값을 정리하려면:

```powershell
py -m market_briefing_bot prepare-cloud-secrets
```

## 문제가 생기면

```powershell
py -m market_briefing_bot readiness
py -m market_briefing_bot setup-next
py -m market_briefing_bot doctor
```

`readiness`는 설정 파일, 카카오 키, 로그인 토큰, 자동 실행 등록 여부를 한 번에 보여줍니다.

카카오 오류가 나오면 대부분 Redirect URI, 메시지 권한, 링크 도메인 셋 중 하나입니다. 자세한 해결법은 `docs\KAKAO_SETUP.md`에 적어 두었습니다.

자세한 설명은 `README.md`와 `docs\KAKAO_SETUP.md`에 있습니다.
