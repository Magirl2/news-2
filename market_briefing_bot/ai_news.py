from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from .news import (
    NewsItem,
    korean_news_checkpoints,
    korean_news_plain_explanation,
    korean_news_sentiment,
    korean_news_thinking_frame,
    korean_news_why_it_matters,
)


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


@dataclass(frozen=True)
class NewsInterpretation:
    core_summary: str
    investment_read: str
    risks: str
    checkpoints: list[str]
    source: str


def rule_based_news_interpretation(item: NewsItem) -> NewsInterpretation:
    sentiment, sentiment_reason = korean_news_sentiment(item)
    checkpoints = korean_news_checkpoints(item)
    return NewsInterpretation(
        core_summary=korean_news_plain_explanation(item),
        investment_read=f"{sentiment}: {sentiment_reason} {korean_news_thinking_frame(item)}",
        risks=korean_news_why_it_matters(item),
        checkpoints=checkpoints[:4] or ["다음 거래일 가격과 거래량 반응 확인"],
        source="규칙 기반",
    )


def _safe_text(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = " ".join(value.split()).strip()
    return cleaned or fallback


def _safe_checkpoints(value: object, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return fallback
    checkpoints = [_safe_text(item, "") for item in value]
    checkpoints = [item for item in checkpoints if item]
    return checkpoints[:4] or fallback


def _response_output_text(payload: dict) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    chunks: list[str] = []
    for output in payload.get("output", []):
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "\n".join(chunks).strip()


def _parse_json_text(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def _openai_interpretation(
    item: NewsItem,
    *,
    api_key: str,
    model: str,
    timeout_seconds: int = 25,
) -> NewsInterpretation:
    fallback = rule_based_news_interpretation(item)
    prompt = (
        "You are a professional US equity market analyst writing in Korean for an individual investor.\n"
        "Analyze this news item for next-session investing decisions. Do not give guaranteed advice.\n"
        "Return only JSON with these keys: core_summary, investment_read, risks, checkpoints.\n"
        "checkpoints must be a list of 3 or 4 short Korean strings.\n\n"
        f"Title: {item.title}\n"
        f"Description: {item.description}\n"
        f"Source: {item.source}\n"
        f"Published: {item.published}\n"
        f"URL: {item.link}\n"
    )
    body = json.dumps(
        {
            "model": model,
            "input": [
                {
                    "role": "developer",
                    "content": "Write concise Korean investment analysis. Prefer concrete signals and risks.",
                },
                {"role": "user", "content": prompt},
            ],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    parsed = _parse_json_text(_response_output_text(payload))
    return NewsInterpretation(
        core_summary=_safe_text(parsed.get("core_summary"), fallback.core_summary),
        investment_read=_safe_text(parsed.get("investment_read"), fallback.investment_read),
        risks=_safe_text(parsed.get("risks"), fallback.risks),
        checkpoints=_safe_checkpoints(parsed.get("checkpoints"), fallback.checkpoints),
        source="OpenAI",
    )


def build_news_interpretations(
    news_items: list[NewsItem],
    *,
    api_key: str = "",
    model: str = "gpt-5.5",
    timeout_seconds: int = 25,
) -> tuple[dict[str, NewsInterpretation], list[str]]:
    warnings: list[str] = []
    interpretations: dict[str, NewsInterpretation] = {}
    if not api_key.strip():
        return {item.link: rule_based_news_interpretation(item) for item in news_items}, warnings

    for item in news_items:
        try:
            interpretations[item.link] = _openai_interpretation(
                item,
                api_key=api_key.strip(),
                model=model or "gpt-5.5",
                timeout_seconds=timeout_seconds,
            )
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            interpretations[item.link] = rule_based_news_interpretation(item)
            warnings.append(f"OpenAI 뉴스 해석 실패, 규칙 기반으로 대체: {item.title[:60]} ({exc})")
    return interpretations, warnings
