from __future__ import annotations

import argparse

from .analyzer import analyze_symbol
from .commands import parse_command
from .formatter import format_analysis
from .telegram_app import run_polling


def cmd_analyze(args: argparse.Namespace) -> int:
    print(format_analysis(analyze_symbol(args.symbol, include_news=not args.no_news)))
    return 0


def cmd_parse(args: argparse.Namespace) -> int:
    print(parse_command(args.text))
    return 0


def cmd_run_telegram(_args: argparse.Namespace) -> int:
    run_polling()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram live market helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="종목 최신 분석")
    analyze.add_argument("symbol")
    analyze.add_argument("--no-news", action="store_true", help="뉴스 조회 없이 가격/차트만 분석")
    analyze.set_defaults(func=cmd_analyze)

    parse = subparsers.add_parser("parse", help="명령 파싱 테스트")
    parse.add_argument("text")
    parse.set_defaults(func=cmd_parse)

    run = subparsers.add_parser("run-telegram", help="텔레그램 봇 실행")
    run.set_defaults(func=cmd_run_telegram)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

