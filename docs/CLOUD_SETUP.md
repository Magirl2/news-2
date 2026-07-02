# 클라우드 자동 실행 선택지

처음에는 Windows 작업 스케줄러를 추천합니다. 설정이 가장 쉽고 바로 확인할 수 있기 때문입니다.

하지만 컴퓨터가 꺼져 있어도 매일 실행되게 하고 싶다면 GitHub Actions로 옮길 수 있습니다.

## 언제 클라우드가 좋은가요?

- 컴퓨터를 매일 켜두기 어렵다.
- 여행 중에도 브리핑을 받고 싶다.
- 자동 실행 실패 기록을 웹에서 보고 싶다.

## 필요한 것

- GitHub 계정
- 이 프로젝트를 올릴 GitHub 저장소
- Kakao Developers REST API 키
- 카카오 로그인 후 만들어지는 토큰 JSON

## 이미 준비된 파일

아래 파일이 GitHub Actions 자동 실행 템플릿입니다.

```text
.github/workflows/us-market-briefing.yml
```

기본 실행 시간은 한국 시간 오전 7시 10분입니다. 미국장이 서머타임이어도 정규장 마감 이후입니다.

## GitHub Secrets에 넣을 값

GitHub 저장소의 `Settings > Secrets and variables > Actions`에 아래 값을 넣습니다.

```text
KAKAO_REST_API_KEY
KAKAO_TOKENS_JSON
```

`KAKAO_REST_API_KEY`는 Kakao Developers의 REST API 키입니다.

`KAKAO_TOKENS_JSON`은 내 컴퓨터에서 카카오 로그인을 마친 뒤 생기는 아래 파일의 전체 내용입니다.

```text
.secrets\kakao_tokens.json
```

직접 열어서 복사하기 어렵다면 아래 명령을 실행합니다.

```powershell
py -m market_briefing_bot prepare-cloud-secrets
```

그러면 아래 파일이 만들어집니다.

```text
.secrets\github_actions_secrets.txt
```

이 파일에는 GitHub Secrets에 넣을 이름과 값이 정리되어 있습니다. 비밀값이 들어 있으니 다른 사람에게 보내거나 GitHub 코드에 올리지 마세요.

민감한 값이므로 GitHub 코드 파일에 붙여넣지 말고 반드시 Secret에만 넣어야 합니다.

## 실행 확인

GitHub 저장소에서 `Actions` 탭을 열고 `US Market Kakao Briefing` 워크플로를 선택합니다.

처음에는 `Run workflow` 버튼으로 수동 실행해 테스트합니다. 카카오톡 `나와의 채팅`에 메시지가 오면 성공입니다.

## 주의

- 카카오 refresh token은 언젠가 만료될 수 있습니다. 그때는 내 컴퓨터에서 `py -m market_briefing_bot kakao-login`을 다시 실행하고, 새 `.secrets\kakao_tokens.json` 내용을 GitHub Secret에 다시 넣습니다.
- GitHub Actions는 인터넷에서 실행되므로 비밀값을 로그에 출력하지 않도록 조심해야 합니다. 현재 봇은 키 값을 화면에 표시하지 않습니다.
- 처음 설정이 어렵다면 Windows 작업 스케줄러 방식으로 충분히 시작할 수 있습니다.
