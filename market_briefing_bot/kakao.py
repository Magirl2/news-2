from __future__ import annotations

import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from .config import TOKEN_FILE, Config


AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_ME_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"


class KakaoError(RuntimeError):
    pass


def explain_kakao_error(status_code: int, body: str) -> str:
    lower_body = body.lower()
    hints: list[str] = []

    if "koe006" in lower_body or "redirect_uri" in lower_body:
        hints.append(
            "Redirect URI가 맞지 않을 수 있습니다. Kakao Developers와 .env의 "
            "KAKAO_REDIRECT_URI가 http://localhost:8765/callback 으로 같은지 확인해 주세요."
        )
    if "koe010" in lower_body or "bad client credentials" in lower_body:
        hints.append(
            "REST API 키가 틀렸거나 Kakao Developers에서 Client Secret 기능이 켜져 있을 수 있습니다. "
            "앱 키 메뉴의 REST API 키를 다시 확인하고, 카카오 로그인 > 보안에서 Client Secret을 끄거나 "
            ".env의 KAKAO_CLIENT_SECRET에 Client Secret 값을 넣어 주세요."
        )
    if "invalid_grant" in lower_body:
        hints.append(
            "인증 코드가 만료됐거나 이미 사용됐을 수 있습니다. "
            "py -m market_briefing_bot kakao-login 을 다시 실행해 주세요."
        )
    if "insufficient_scope" in lower_body or "talk_message" in lower_body:
        hints.append(
            "카카오톡 메시지 전송 권한이 빠졌을 수 있습니다. Kakao Developers의 동의 항목에서 "
            "카카오톡 메시지 전송 권한을 켠 뒤 kakao-login을 다시 실행해 주세요."
        )
    if "invalid_token" in lower_body or status_code == 401:
        hints.append(
            "카카오 로그인 토큰이 만료됐거나 올바르지 않을 수 있습니다. "
            "py -m market_briefing_bot kakao-login 을 다시 실행해 주세요."
        )
    if "domain" in lower_body or "url" in lower_body:
        hints.append(
            "메시지 안의 링크 도메인이 카카오 앱 설정에 등록되지 않았을 수 있습니다. "
            "Kakao Developers의 플랫폼/Web 사이트 도메인에 finance.yahoo.com 또는 "
            ".env의 KAKAO_LINK_URL 도메인을 등록해 주세요."
        )
    if status_code == 429 or "quota" in lower_body or "limit" in lower_body:
        hints.append("카카오 API 호출 한도에 걸렸을 수 있습니다. 잠시 뒤 다시 시도해 주세요.")

    if not hints:
        hints.append(
            "Kakao Developers의 앱 키, Redirect URI, 메시지 권한, 링크 도메인 설정을 확인해 주세요."
        )

    return " ".join(hints)


def _post_form(url: str, data: dict[str, str], headers: dict[str, str] | None = None) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            "User-Agent": "Mozilla/5.0 (compatible; market-briefing-bot/0.1)",
            **(headers or {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        explanation = explain_kakao_error(exc.code, body)
        raise KakaoError(f"Kakao API 오류: HTTP {exc.code}. {explanation} 원문: {body}") from exc


def _load_tokens(token_file: Path = TOKEN_FILE) -> dict[str, Any]:
    raw_tokens = os.environ.get("KAKAO_TOKENS_JSON", "").strip()
    if raw_tokens:
        try:
            return json.loads(raw_tokens)
        except json.JSONDecodeError as exc:
            raise KakaoError("KAKAO_TOKENS_JSON 값이 올바른 JSON 형식이 아닙니다.") from exc
    if not token_file.exists():
        return {}
    return json.loads(token_file.read_text(encoding="utf-8"))


def _save_tokens(tokens: dict[str, Any], token_file: Path = TOKEN_FILE) -> None:
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(json.dumps(tokens, ensure_ascii=False, indent=2), encoding="utf-8")


def build_auth_url(config: Config, state: str | None = None) -> str:
    if not config.kakao_rest_api_key:
        raise KakaoError(".env 파일에 KAKAO_REST_API_KEY를 먼저 넣어 주세요.")
    params = {
        "response_type": "code",
        "client_id": config.kakao_rest_api_key,
        "redirect_uri": config.kakao_redirect_uri,
        "scope": "talk_message",
    }
    if state:
        params["state"] = state
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(config: Config, code: str) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "client_id": config.kakao_rest_api_key,
        "redirect_uri": config.kakao_redirect_uri,
        "code": code,
    }
    if config.kakao_client_secret:
        data["client_secret"] = config.kakao_client_secret
    tokens = _post_form(TOKEN_URL, data)
    if "access_token" not in tokens:
        raise KakaoError("카카오 토큰을 받지 못했습니다. REST API 키와 Redirect URI를 확인해 주세요.")
    _save_tokens(tokens)
    return tokens


def refresh_access_token(config: Config, tokens: dict[str, Any]) -> dict[str, Any]:
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise KakaoError("refresh_token이 없습니다. kakao-login을 다시 실행해 주세요.")
    data = {
        "grant_type": "refresh_token",
        "client_id": config.kakao_rest_api_key,
        "refresh_token": refresh_token,
    }
    if config.kakao_client_secret:
        data["client_secret"] = config.kakao_client_secret
    refreshed = _post_form(TOKEN_URL, data)
    tokens.update(refreshed)
    _save_tokens(tokens)
    return tokens


def split_message(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    current = ""

    def is_standalone_block(block: str) -> bool:
        return block.startswith("뉴스 ")

    def flush_current() -> None:
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    def append_block(block: str) -> None:
        nonlocal current
        if is_standalone_block(block):
            flush_current()
            if len(block) <= max_chars:
                chunks.append(block)
                return
            append_long_block(block)
            flush_current()
            return
        candidate = f"{current}\n\n{block}".strip() if current else block
        if len(candidate) <= max_chars:
            current = candidate
            return
        flush_current()
        if len(block) <= max_chars:
            current = block
            return
        append_long_block(block)

    def append_long_block(block: str) -> None:
        nonlocal current
        for line in block.splitlines():
            append_line(line)

    def append_line(line: str) -> None:
        nonlocal current
        candidate = f"{current}\n{line}".strip() if current else line
        if len(candidate) <= max_chars:
            current = candidate
            return
        if current:
            chunks.append(current)
        while len(line) > max_chars:
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        current = line

    for block in text.split("\n\n"):
        block = block.strip()
        if block:
            append_block(block)
    if current:
        chunks.append(current)
    return chunks


class KakaoClient:
    def __init__(self, config: Config):
        self.config = config

    def _access_token(self) -> str:
        tokens = _load_tokens()
        access_token = tokens.get("access_token")
        if access_token:
            return access_token
        tokens = refresh_access_token(self.config, tokens)
        return tokens["access_token"]

    def _send_chunk(self, text: str) -> None:
        template_object = {
            "object_type": "text",
            "text": text,
            "link": {
                "web_url": self.config.kakao_link_url,
                "mobile_web_url": self.config.kakao_link_url,
            },
            "button_title": "시장 보기",
        }
        data = {"template_object": json.dumps(template_object, ensure_ascii=False)}
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        _post_form(SEND_ME_URL, data, headers=headers)

    def send_text(self, text: str) -> int:
        body_limit = max(80, self.config.kakao_chunk_size - 12)
        chunks = split_message(text, body_limit)
        if not chunks:
            return 0
        for index, chunk in enumerate(chunks, start=1):
            prefix = f"({index}/{len(chunks)})\n" if len(chunks) > 1 else ""
            try:
                self._send_chunk(prefix + chunk)
            except KakaoError as exc:
                if "401" not in str(exc):
                    raise
                tokens = refresh_access_token(self.config, _load_tokens())
                if not tokens.get("access_token"):
                    raise
                self._send_chunk(prefix + chunk)
            if index < len(chunks):
                time.sleep(0.4)
        return len(chunks)


def run_local_login(config: Config, timeout_seconds: int = 180) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(config.kakao_redirect_uri)
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise KakaoError("자동 로그인은 localhost Redirect URI에서만 사용할 수 있습니다.")
    port = parsed.port or 80
    expected_path = parsed.path or "/"
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed_path = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed_path.query)
            if parsed_path.path != expected_path:
                self.send_error(404)
                return
            if params.get("state", [""])[0] != state:
                self.send_error(400, "state mismatch")
                return
            code = params.get("code", [""])[0]
            error = params.get("error_description", params.get("error", [""]))[0]
            if code:
                try:
                    exchange_code(config, code)
                    result["ok"] = "1"
                    body = "카카오 연결과 토큰 저장이 끝났습니다. 이 창은 닫아도 됩니다."
                    self.send_response(200)
                except KakaoError as exc:
                    result["error"] = str(exc)
                    body = f"카카오 연결 실패: {exc}"
                    self.send_response(400)
            else:
                result["error"] = error or "인증 코드가 없습니다."
                body = f"카카오 연결 실패: {result['error']}"
                self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

    server = HTTPServer((parsed.hostname or "localhost", port), CallbackHandler)
    server.timeout = 1
    print("아래 주소를 브라우저에 붙여넣고 카카오 로그인을 승인해 주세요:")
    print(build_auth_url(config, state=state))

    deadline = time.time() + timeout_seconds
    while time.time() < deadline and "ok" not in result and "error" not in result:
        server.handle_request()

    server.server_close()
    if "error" in result:
        raise KakaoError(result["error"])
    if "ok" not in result:
        raise KakaoError("제한 시간 안에 카카오 로그인이 끝나지 않았습니다.")
    return _load_tokens()
