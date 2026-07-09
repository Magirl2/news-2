from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import subprocess
import sys
from urllib.parse import urlparse

from .briefing import Briefing, build_briefing
from .config import ENV_FILE, LOGS_DIR, REPORTS_DIR, SEND_STATE_FILE, TOKEN_FILE, ensure_project_dirs, load_config
from .kakao import KakaoClient, KakaoError, build_auth_url, exchange_code, run_local_login
from .market_calendar import current_market_note, last_completed_trading_day


CLOUD_SECRETS_FILE = TOKEN_FILE.parent / "github_actions_secrets.txt"


def _setup_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOGS_DIR / "bot.log",
        level=logging.INFO,
        encoding="utf-8",
        format="%(asctime)s %(levelname)s %(message)s",
    )


def _print_next_steps_for_kakao() -> None:
    print("카카오톡 연결이 아직 끝나지 않았습니다.")
    print("1. py -m market_briefing_bot configure-kakao 로 Kakao REST API 키를 저장해 주세요.")
    print("2. py -m market_briefing_bot kakao-login 을 실행해 주세요.")
    print("3. py -m market_briefing_bot send-test 로 테스트해 주세요.")


def _has_kakao_token() -> bool:
    raw_tokens = os.environ.get("KAKAO_TOKENS_JSON", "").strip()
    if raw_tokens:
        try:
            data = json.loads(raw_tokens)
        except json.JSONDecodeError:
            return False
        return bool(data.get("access_token") or data.get("refresh_token"))
    if not TOKEN_FILE.exists():
        return False
    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("access_token") or data.get("refresh_token"))


def _next_setup_step(config) -> str:
    has_config_source = ENV_FILE.exists() or bool(os.environ.get("KAKAO_REST_API_KEY"))
    if not has_config_source:
        return "먼저 Copy-Item .env.example .env 명령으로 설정 파일을 만들어 주세요."
    if not config.kakao_rest_api_key:
        return "py -m market_briefing_bot configure-kakao 명령으로 Kakao REST API 키를 저장해 주세요."
    if not _has_kakao_token():
        return "py -m market_briefing_bot kakao-login 명령으로 카카오 로그인을 연결해 주세요."
    return "py -m market_briefing_bot send-test 명령으로 카카오톡 테스트 메시지를 보내 보세요."


def _scheduled_task_status() -> str:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        "$t = Get-ScheduledTask -TaskName 'US Market Kakao Briefing' -ErrorAction SilentlyContinue; "
        "if ($null -eq $t) { '없음' } else { '있음: ' + $t.State }",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "확인 못 함"
    output = (result.stdout or "").strip()
    return output or "확인 못 함"


def _print_check(label: str, ok: bool, detail: str = "") -> None:
    mark = "OK" if ok else "필요"
    suffix = f" - {detail}" if detail else ""
    print(f"[{mark}] {label}{suffix}")


def _write_env_values(updates: dict[str, str]) -> None:
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    found_keys = set()
    updated_lines = []
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            updated_lines.append(line)
            continue
        key, _value = line.split("=", 1)
        key = key.strip()
        if key in updates:
            updated_lines.append(f"{key}={updates[key]}")
            found_keys.add(key)
        else:
            updated_lines.append(line)

    for key, value in updates.items():
        if key not in found_keys:
            updated_lines.append(f"{key}={value}")

    ENV_FILE.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")


def _build_github_secrets_text(rest_api_key: str, token_data: dict) -> str:
    compact_tokens = json.dumps(token_data, ensure_ascii=False, separators=(",", ":"))
    return "\n".join(
        [
            "GitHub Actions Secrets",
            "",
            "GitHub 저장소의 Settings > Secrets and variables > Actions 에 아래 값을 추가하세요.",
            "",
            "[필수]",
            "Secret name: KAKAO_REST_API_KEY",
            "Secret value:",
            rest_api_key,
            "",
            "Secret name: KAKAO_TOKENS_JSON",
            "Secret value:",
            compact_tokens,
            "",
            "[선택 - 보고서 품질 개선]",
            "Secret name: WATCHLIST_SYMBOLS",
            "Secret value example:",
            "NVDA,AAPL,TSLA,MSFT",
            "",
            "Secret name: FRED_API_KEY",
            "Secret value:",
            "FRED에서 발급받은 API 키",
            "",
            "Secret name: ALPHA_VANTAGE_API_KEY",
            "Secret value:",
            "Alpha Vantage에서 발급받은 API 키",
            "",
            "Secret name: SEC_USER_AGENT",
            "Secret value example:",
            "market-briefing-bot your-email@example.com",
            "",
            "발급/적용 방법은 docs/OPTIONAL_KEYS.md 를 보세요.",
            "",
            "주의: 이 파일에는 카카오 비밀값이 들어 있습니다. 다른 사람에게 보내거나 GitHub 코드에 올리지 마세요.",
        ]
    )


def _read_send_state() -> dict:
    if not SEND_STATE_FILE.exists():
        return {}
    try:
        return json.loads(SEND_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_send_state(state: dict) -> None:
    SEND_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEND_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _mark_send_success(target_date: str, sent_count: int, report_path: str, html_path: str) -> None:
    state = _read_send_state()
    state["last_success"] = {
        "target_date": target_date,
        "sent_count": sent_count,
        "report_path": report_path,
        "html_path": html_path,
    }
    state.pop("last_failure", None)
    _write_send_state(state)


def _mark_send_failure(target_date: str | None, error: Exception) -> None:
    state = _read_send_state()
    state["last_failure"] = {
        "target_date": target_date,
        "error": str(error),
    }
    _write_send_state(state)


def _short_error(error: object, limit: int = 260) -> str:
    text = " ".join(str(error).split())
    if not text:
        return "원인을 확인하지 못했습니다."
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _failure_action(error: object, location: str = "") -> str:
    text = f"{location} {error}".lower()
    if "access token" in text or "invalid_token" in text or "401" in text:
        return "카카오 로그인 토큰이 만료됐을 가능성이 큽니다. PC에서 py -m market_briefing_bot kakao-login을 다시 실행한 뒤 새 KAKAO_TOKENS_JSON을 GitHub Secrets에 넣어 주세요."
    if "kakao_rest_api_key" in text or "rest api" in text or "koe010" in text:
        return "GitHub Secrets의 KAKAO_REST_API_KEY가 맞는지 확인해 주세요. Kakao Developers의 REST API 키를 넣어야 합니다."
    if "talk_message" in text or "insufficient_scope" in text:
        return "Kakao Developers 동의항목에서 카카오톡 메시지 전송 권한을 켠 뒤 kakao-login을 다시 실행해 주세요."
    if "report" in text or "briefing" in text or "preview" in text or "build" in text:
        return "보고서 생성 단계 문제일 수 있습니다. GitHub Actions의 market-briefing-reports artifact와 logs/bot.log를 확인해 주세요."
    if "pages" in text or "deploy" in text:
        return "GitHub Pages 배포 단계 문제일 수 있습니다. 저장소 Settings > Pages와 Actions 권한을 확인해 주세요."
    if "timeout" in text or "timed out" in text:
        return "외부 데이터 응답이 늦었을 수 있습니다. 잠시 뒤 GitHub Actions에서 Re-run jobs를 눌러 다시 실행해 주세요."
    if "rss" in text or "yahoo" in text or "stooq" in text or "fred" in text:
        return "외부 데이터 출처 문제일 수 있습니다. 보고서의 확인 필요 섹션과 GitHub Actions 로그를 확인해 주세요."
    return "GitHub Actions 로그에서 실패한 단계의 빨간 줄을 확인하고, 필요한 Secret 값이 빠졌는지 먼저 봐 주세요."


def _github_run_url() -> str:
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com").strip().rstrip("/")
    if repository and run_id:
        return f"{server_url}/{repository}/actions/runs/{run_id}"
    return ""


def _build_failure_alert_text(
    *,
    location: str,
    error: object,
    run_url: str = "",
    rerun_hint: str = "GitHub Actions 화면에서 Re-run jobs를 눌러 다시 실행하세요.",
) -> str:
    run_url = run_url or _github_run_url()
    lines = [
        "미국장 브리핑 자동화 실패",
        f"실패 위치: {location or '위치 확인 필요'}",
        f"원인: {_short_error(error)}",
        f"내가 해야 할 일: {_failure_action(error, location)}",
        f"다시 실행 방법: {rerun_hint}",
    ]
    if run_url:
        lines.append(f"로그 링크: {run_url}")
    return "\n".join(lines)


def _send_failure_alert(
    config,
    *,
    location: str,
    error: object,
    run_url: str = "",
    rerun_hint: str = "GitHub Actions 화면에서 Re-run jobs를 눌러 다시 실행하세요.",
) -> bool:
    message = _build_failure_alert_text(
        location=location,
        error=error,
        run_url=run_url,
        rerun_hint=rerun_hint,
    )
    if not _has_kakao_token():
        logging.warning("Kakao token is missing. Failure alert was not sent: %s", message)
        print(message)
        return False
    try:
        KakaoClient(config).send_text(message)
        logging.info("Failure alert sent to KakaoTalk: %s", location)
        return True
    except Exception:
        logging.exception("Failed to send Kakao failure alert")
        print(message)
        return False


def _already_sent(target_date: str) -> bool:
    state = _read_send_state()
    return state.get("last_success", {}).get("target_date") == target_date


def _public_report_url(config, briefing) -> str:
    base_url = config.report_public_base_url.strip()
    if not base_url:
        return ""
    return f"{base_url.rstrip('/')}/{briefing.html_path.name}"


def _kakao_delivery_text(config, briefing) -> str:
    report_url = _public_report_url(config, briefing)
    if config.kakao_send_mode != "link" or not report_url:
        return briefing.text

    lines = [line.strip() for line in briefing.text.splitlines() if line.strip()]
    summary_lines = lines[:3] if lines else ["미국장 마감 보고서"]
    text = "\n".join(summary_lines + ["전체 보고서:", report_url])
    if len(text) <= config.kakao_chunk_size:
        return text
    title = summary_lines[0] if summary_lines else "미국장 마감 보고서"
    return "\n".join([title, "전체 보고서:", report_url])


def _latest_built_briefing() -> Briefing | None:
    html_reports = sorted(REPORTS_DIR.glob("*_briefing.html"))
    if not html_reports:
        return None

    html_path = html_reports[-1]
    report_path = html_path.with_suffix(".md")
    if report_path.exists():
        text = report_path.read_text(encoding="utf-8")
    else:
        target_date = html_path.stem.replace("_briefing", "")
        text = f"미국장 마감 {target_date}\n전체 보고서는 아래 링크에서 확인하세요."
    return Briefing(text=text, report_path=report_path, html_path=html_path, sources=[], warnings=[])


def cmd_preview(args: argparse.Namespace) -> int:
    config = load_config()
    briefing = build_briefing(config)
    logging.info("Preview report created: %s", briefing.report_path)
    print(briefing.text)
    print()
    print(f"보고서 파일: {briefing.report_path}")
    print(f"HTML 보고서: {briefing.html_path}")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    config = load_config()
    target_date: str | None = None
    try:
        briefing = build_briefing(config)
        target_date = briefing.report_path.stem.replace("_briefing", "")
        if not _has_kakao_token():
            _print_next_steps_for_kakao()
            print(f"미리보기 보고서는 만들었습니다: {briefing.report_path}")
            print(f"HTML 보고서도 만들었습니다: {briefing.html_path}")
            logging.warning("Kakao token file is missing. Report was created only.")
            return 2
        sent_count = KakaoClient(config).send_text(_kakao_delivery_text(config, briefing))
        _mark_send_success(target_date, sent_count, str(briefing.report_path), str(briefing.html_path))
        logging.info("Report sent to KakaoTalk in %s chunks: %s", sent_count, briefing.report_path)
        print(f"카카오톡으로 {sent_count}개 메시지를 보냈습니다.")
        print(f"보고서 파일: {briefing.report_path}")
        print(f"HTML 보고서: {briefing.html_path}")
        return 0
    except Exception as exc:
        _mark_send_failure(target_date, exc)
        logging.exception("Daily briefing send failed")
        if _has_kakao_token():
            try:
                KakaoClient(config).send_text(
                    "미국장 브리핑 봇 실행 중 오류가 났습니다.\n"
                    f"원인: {exc}\n"
                    "자세한 내용은 logs\\bot.log 파일을 확인해 주세요."
                )
            except Exception:
                logging.exception("Failed to send Kakao failure alert")
        raise


def cmd_send_built(args: argparse.Namespace) -> int:
    config = load_config()
    briefing = _latest_built_briefing()
    if briefing is None:
        print("보낼 보고서가 없습니다. 먼저 preview 명령으로 보고서를 만들어 주세요.")
        return 2

    target_date = briefing.html_path.stem.replace("_briefing", "")
    try:
        if not _has_kakao_token():
            _print_next_steps_for_kakao()
            print(f"이미 만들어진 보고서: {briefing.html_path}")
            logging.warning("Kakao token file is missing. Built report was not sent.")
            return 2
        sent_count = KakaoClient(config).send_text(_kakao_delivery_text(config, briefing))
        _mark_send_success(target_date, sent_count, str(briefing.report_path), str(briefing.html_path))
        logging.info("Built report sent to KakaoTalk in %s chunks: %s", sent_count, briefing.html_path)
        print(f"카카오톡으로 {sent_count}개 메시지를 보냈습니다.")
        print(f"HTML 보고서: {briefing.html_path}")
        return 0
    except Exception as exc:
        _mark_send_failure(target_date, exc)
        logging.exception("Built daily briefing send failed")
        if _has_kakao_token():
            try:
                KakaoClient(config).send_text(
                    "미국장 브리핑 발송 중 오류가 났습니다.\n"
                    f"원인: {exc}\n"
                    "GitHub Actions 로그를 확인해 주세요."
                )
            except Exception:
                logging.exception("Failed to send Kakao failure alert")
        raise


def cmd_send_once(args: argparse.Namespace) -> int:
    config = load_config()
    target_date: str | None = None
    try:
        briefing = build_briefing(config)
        target_date = briefing.report_path.stem.replace("_briefing", "")
        if _already_sent(target_date):
            print(f"이미 {target_date} 미국장 브리핑을 보냈습니다. 중복 발송을 건너뜁니다.")
            logging.info("Skipped duplicate scheduled send for %s.", target_date)
            return 0
        if not _has_kakao_token():
            _print_next_steps_for_kakao()
            print(f"미리보기 보고서는 만들었습니다: {briefing.report_path}")
            print(f"HTML 보고서도 만들었습니다: {briefing.html_path}")
            logging.warning("Kakao token file is missing. Scheduled report was created only.")
            return 2
        sent_count = KakaoClient(config).send_text(_kakao_delivery_text(config, briefing))
        _mark_send_success(target_date, sent_count, str(briefing.report_path), str(briefing.html_path))
        logging.info("Scheduled report sent to KakaoTalk in %s chunks: %s", sent_count, briefing.report_path)
        print(f"카카오톡으로 {sent_count}개 메시지를 보냈습니다.")
        print(f"보고서 파일: {briefing.report_path}")
        print(f"HTML 보고서: {briefing.html_path}")
        return 0
    except Exception as exc:
        _mark_send_failure(target_date, exc)
        logging.exception("Scheduled daily briefing send failed")
        if _has_kakao_token():
            try:
                KakaoClient(config).send_text(
                    "미국장 브리핑 자동 발송 실패\n"
                    f"원인: {exc}\n"
                    "자세한 내용은 logs\\bot.log 와 logs\\scheduled-task.log 를 확인해 주세요."
                )
            except Exception:
                logging.exception("Failed to send Kakao scheduled failure alert")
        raise


def cmd_send_test(args: argparse.Namespace) -> int:
    config = load_config()
    if not _has_kakao_token():
        _print_next_steps_for_kakao()
        return 2
    text = (
        "미국장 브리핑 봇 테스트 메시지입니다.\n"
        "이 메시지가 보이면 카카오톡 연결은 정상입니다."
    )
    sent_count = KakaoClient(config).send_text(text)
    logging.info("Kakao test message sent in %s chunks.", sent_count)
    print(f"테스트 메시지 {sent_count}개를 보냈습니다.")
    return 0


def cmd_notify_failure(args: argparse.Namespace) -> int:
    config = load_config()
    location = args.location or os.environ.get("FAILURE_LOCATION", "GitHub Actions")
    error = args.error or os.environ.get("FAILURE_ERROR", "자동 실행 중 한 단계가 실패했습니다.")
    run_url = args.run_url or os.environ.get("FAILURE_RUN_URL", "") or _github_run_url()
    rerun_hint = args.rerun_hint or "GitHub Actions 화면에서 Re-run jobs를 눌러 다시 실행하세요."
    _mark_send_failure(None, RuntimeError(f"{location}: {error}"))
    _send_failure_alert(
        config,
        location=location,
        error=error,
        run_url=run_url,
        rerun_hint=rerun_hint,
    )
    return 0


def _send_state_summary() -> str:
    state = _read_send_state()
    success = state.get("last_success")
    failure = state.get("last_failure")
    parts = []
    if success:
        parts.append(
            f"마지막 성공: {success.get('target_date')} / {success.get('sent_count')}개 메시지"
        )
    if failure:
        parts.append(f"마지막 실패: {failure.get('target_date') or '날짜 미확인'} / {failure.get('error')}")
    return " / ".join(parts) if parts else "기록 없음"


def cmd_send_status(args: argparse.Namespace) -> int:
    print("발송 상태")
    print(f"- 상태 파일: {SEND_STATE_FILE}")
    print(f"- {_send_state_summary()}")
    return 0


def cmd_kakao_auth_url(args: argparse.Namespace) -> int:
    config = load_config()
    print(build_auth_url(config))
    return 0


def cmd_kakao_exchange_code(args: argparse.Namespace) -> int:
    config = load_config()
    exchange_code(config, args.code)
    print("카카오 토큰을 저장했습니다. 이제 send-test를 실행할 수 있습니다.")
    return 0


def cmd_kakao_login(args: argparse.Namespace) -> int:
    config = load_config()
    run_local_login(config)
    print("카카오 연결이 끝났습니다. 이제 send-test를 실행해 주세요.")
    return 0


def cmd_configure_kakao(args: argparse.Namespace) -> int:
    rest_key = getpass.getpass("Kakao REST API 키를 붙여넣고 Enter를 누르세요: ").strip()
    if not rest_key:
        print("입력된 키가 없어 저장하지 않았습니다.")
        return 2

    _write_env_values(
        {
            "KAKAO_REST_API_KEY": rest_key,
            "KAKAO_REDIRECT_URI": "http://localhost:8765/callback",
            "KAKAO_LINK_URL": "https://finance.yahoo.com/markets",
        }
    )
    print(".env 파일에 카카오 기본 설정을 저장했습니다.")
    print("다음 명령을 실행해 카카오 로그인을 연결해 주세요:")
    print("py -m market_briefing_bot kakao-login")
    return 0


def cmd_prepare_cloud_secrets(args: argparse.Namespace) -> int:
    config = load_config()
    if not config.kakao_rest_api_key:
        print("먼저 py -m market_briefing_bot configure-kakao 로 Kakao REST API 키를 저장해 주세요.")
        return 2
    if not TOKEN_FILE.exists():
        print("먼저 py -m market_briefing_bot kakao-login 으로 카카오 로그인을 연결해 주세요.")
        return 2
    try:
        token_data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"카카오 토큰 파일 형식이 올바르지 않습니다: {TOKEN_FILE}")
        return 2
    if not token_data.get("refresh_token"):
        print("카카오 refresh_token이 없습니다. py -m market_briefing_bot kakao-login 을 다시 실행해 주세요.")
        return 2

    CLOUD_SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CLOUD_SECRETS_FILE.write_text(
        _build_github_secrets_text(config.kakao_rest_api_key, token_data),
        encoding="utf-8",
    )
    print("GitHub Actions용 Secret 정리 파일을 만들었습니다.")
    print(f"파일 위치: {CLOUD_SECRETS_FILE}")
    print("이 파일의 값을 GitHub Secrets에 붙여넣은 뒤, 파일은 안전한 곳에 보관하거나 삭제해도 됩니다.")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config()
    parsed_redirect = urlparse(config.kakao_redirect_uri)
    parsed_link = urlparse(config.kakao_link_url)
    print("점검 결과")
    print(f"- 설정 파일: {'있음' if ENV_FILE.exists() else '없음'} ({ENV_FILE})")
    print(f"- 카카오 REST API 키: {'있음' if config.kakao_rest_api_key else '없음'}")
    print(f"- 카카오 토큰 파일: {'있음' if _has_kakao_token() else '없음'} ({TOKEN_FILE})")
    print(f"- Redirect URI: {config.kakao_redirect_uri}")
    print(f"- 메시지 링크: {config.kakao_link_url}")
    if parsed_link.netloc:
        print(f"- 카카오 앱에 등록할 링크 도메인: {parsed_link.netloc}")
    if parsed_redirect.scheme not in {"http", "https"} or not parsed_redirect.netloc:
        print("- 확인 필요: KAKAO_REDIRECT_URI 형식이 올바르지 않아 보입니다.")
    if parsed_link.scheme not in {"http", "https"} or not parsed_link.netloc:
        print("- 확인 필요: KAKAO_LINK_URL은 http 또는 https 주소여야 합니다.")
    if config.kakao_rest_api_key and not _has_kakao_token():
        print("- 확인 필요: 카카오 로그인 연결이 아직 없습니다. talk_message 권한 승인 후 kakao-login을 실행해 주세요.")
    target = last_completed_trading_day(market_timezone=config.market_timezone)
    print(f"- 최신 미국장 기준일: {target}")
    print(f"- 오늘 상태: {current_market_note(market_timezone=config.market_timezone)}")
    print("- 인터넷/데이터 점검은 preview 명령으로 확인합니다.")
    print(f"- 다음 할 일: {_next_setup_step(config)}")
    return 0


def cmd_setup_next(args: argparse.Namespace) -> int:
    config = load_config()
    print(_next_setup_step(config))
    return 0


def cmd_readiness(args: argparse.Namespace) -> int:
    config = load_config()
    parsed_redirect = urlparse(config.kakao_redirect_uri)
    parsed_link = urlparse(config.kakao_link_url)
    has_env = ENV_FILE.exists()
    has_env_config = bool(os.environ.get("KAKAO_REST_API_KEY"))
    has_config_source = has_env or has_env_config
    has_key = bool(config.kakao_rest_api_key)
    has_token = _has_kakao_token()
    redirect_ok = parsed_redirect.scheme in {"http", "https"} and bool(parsed_redirect.netloc)
    link_ok = parsed_link.scheme in {"http", "https"} and bool(parsed_link.netloc)
    is_github_actions = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    task_status = _scheduled_task_status()
    task_ok = task_status.startswith("있음")

    print("준비 상태")
    config_detail = str(ENV_FILE) if has_env else "환경변수 사용"
    _print_check("설정", has_config_source, config_detail)
    _print_check("Kakao REST API 키", has_key, "값은 화면에 표시하지 않습니다")
    _print_check("Kakao Redirect URI", redirect_ok, config.kakao_redirect_uri)
    _print_check("Kakao 메시지 링크", link_ok, config.kakao_link_url)
    if parsed_link.netloc:
        _print_check("카카오 앱 링크 도메인 확인", True, f"Kakao Developers에 {parsed_link.netloc} 등록 필요")
    token_detail = "환경변수 사용" if os.environ.get("KAKAO_TOKENS_JSON", "").strip() else str(TOKEN_FILE)
    _print_check("카카오 로그인 토큰", has_token, token_detail)
    if is_github_actions:
        _print_check("GitHub Actions 실행 환경", True, "Windows 자동 실행 확인은 건너뜁니다")
    else:
        _print_check("Windows 자동 실행", task_ok, task_status)
    _print_check("발송 기록", True, _send_state_summary())
    print()
    print(f"다음 할 일: {_next_setup_step(config)}")
    if has_key and has_token and not task_ok and not is_github_actions:
        print("카카오 테스트가 성공했다면 .\\scripts\\create_windows_task.ps1 로 자동 실행을 켤 수 있습니다.")
    return 0 if has_config_source and has_key and has_token else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="py -m market_briefing_bot",
        description="미국장 마감 브리핑을 만들고 카카오톡으로 보내는 봇",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview = subparsers.add_parser("preview", help="카카오톡 전송 없이 보고서만 미리보기")
    preview.set_defaults(func=cmd_preview)

    send = subparsers.add_parser("send", help="보고서를 만들고 카카오톡으로 보내기")
    send.set_defaults(func=cmd_send)

    send_built = subparsers.add_parser("send-built", help="이미 만들어진 최신 보고서를 카카오톡으로 보내기")
    send_built.set_defaults(func=cmd_send_built)

    send_once = subparsers.add_parser("send-once", help="자동 실행용: 같은 미국장 기준일은 한 번만 보내기")
    send_once.set_defaults(func=cmd_send_once)

    send_status = subparsers.add_parser("send-status", help="마지막 자동/수동 발송 상태 보기")
    send_status.set_defaults(func=cmd_send_status)

    send_test = subparsers.add_parser("send-test", help="카카오톡 테스트 메시지 보내기")
    send_test.set_defaults(func=cmd_send_test)

    notify_failure = subparsers.add_parser("notify-failure", help="GitHub Actions 실패를 카카오톡으로 알리기")
    notify_failure.add_argument("--location", default="", help="실패 위치")
    notify_failure.add_argument("--error", default="", help="실패 원인")
    notify_failure.add_argument("--run-url", default="", help="GitHub Actions 실행 로그 링크")
    notify_failure.add_argument("--rerun-hint", default="", help="다시 실행 방법")
    notify_failure.set_defaults(func=cmd_notify_failure)

    auth_url = subparsers.add_parser("kakao-auth-url", help="카카오 로그인 주소 만들기")
    auth_url.set_defaults(func=cmd_kakao_auth_url)

    exchange = subparsers.add_parser("kakao-exchange-code", help="카카오 인증 코드를 토큰으로 바꾸기")
    exchange.add_argument("code", help="카카오 Redirect URI에 붙은 code 값")
    exchange.set_defaults(func=cmd_kakao_exchange_code)

    login = subparsers.add_parser("kakao-login", help="브라우저 로그인으로 카카오 토큰 저장")
    login.set_defaults(func=cmd_kakao_login)

    configure = subparsers.add_parser("configure-kakao", help="카카오 REST API 키를 .env에 저장")
    configure.set_defaults(func=cmd_configure_kakao)

    cloud_secrets = subparsers.add_parser(
        "prepare-cloud-secrets", help="GitHub Actions Secrets에 넣을 값 파일 만들기"
    )
    cloud_secrets.set_defaults(func=cmd_prepare_cloud_secrets)

    doctor = subparsers.add_parser("doctor", help="설정 상태 점검")
    doctor.set_defaults(func=cmd_doctor)

    setup_next = subparsers.add_parser("setup-next", help="지금 다음에 해야 할 일 보기")
    setup_next.set_defaults(func=cmd_setup_next)

    readiness = subparsers.add_parser("readiness", help="카카오 발송과 자동 실행 준비 상태 확인")
    readiness.set_defaults(func=cmd_readiness)

    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ensure_project_dirs()
    _setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KakaoError as exc:
        logging.exception("Kakao error")
        print(f"카카오 설정을 확인해 주세요: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - show friendly CLI error.
        logging.exception("Unexpected error")
        print(f"실행 중 문제가 생겼습니다: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
