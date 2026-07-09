from __future__ import annotations

import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Iterable, List


KEYWORDS = {
    "fed",
    "rate",
    "rates",
    "inflation",
    "cpi",
    "pce",
    "jobs",
    "payroll",
    "treasury",
    "bond",
    "yields",
    "earnings",
    "revenue",
    "profit",
    "guidance",
    "stocks",
    "market",
    "nasdaq",
    "s&p",
    "dow",
    "oil",
    "gold",
    "dollar",
    "ai",
    "chip",
    "semiconductor",
    "nvidia",
    "apple",
    "microsoft",
    "tesla",
    "amazon",
    "alphabet",
    "meta",
    "defense",
    "budget",
    "hypersonic",
    "weapons",
    "etf",
    "flows",
    "hardware",
    "supplier",
    "suppliers",
    "cloud",
    "compute",
    "valuation",
    "software",
    "salesforce",
    "servicenow",
    "payrolls",
    "employment",
    "adp",
    "dividend",
    "nasdaq-100",
}


LOW_VALUE_NEWS_PATTERNS = (
    "student loan",
    "student loans",
    "homeless",
    "children",
    "your children",
    "lebron",
    "physical disc",
    "lamborghini",
    "bank accounts",
    "what parents need",
    "trump account",
    "trump accounts",
    "trump says outside funds",
    "financial disclosure",
    "streaming",
    "netflix, hulu",
    "personal loans",
    "social security",
    "walmart",
    "solana treasury",
    "best personal loans",
)


@dataclass(frozen=True)
class NewsItem:
    title: str
    link: str
    source: str
    published: str
    score: int
    description: str = ""


def _download_xml(url: str, timeout: int = 20) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; market-briefing-bot/0.1)"
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _score_text(title: str, description: str = "") -> int:
    text = f"{title} {description}".lower()
    if _is_low_value_news(text):
        return -100
    score = sum(2 for keyword in KEYWORDS if _has_keyword(text, keyword))
    if any(_has_keyword(text, name) for name in ("nvidia", "apple", "microsoft", "tesla")):
        score += 2
    if any(_has_keyword(text, word) for word in ("breaking", "live", "stocks")):
        score += 1
    return score


def _is_low_value_news(text: str) -> bool:
    return any(pattern in text for pattern in LOW_VALUE_NEWS_PATTERNS)


def _has_keyword(text: str, keyword: str) -> bool:
    if keyword == "s&p":
        return "s&p" in text or "s&p 500" in text
    if keyword == "trade":
        return re.search(r"\btrade\b", text) is not None
    if len(keyword) <= 3:
        return re.search(rf"\b{re.escape(keyword)}\b", text) is not None
    return re.search(rf"\b{re.escape(keyword)}", text) is not None


def _source_from_root(root: ET.Element, fallback_url: str) -> str:
    channel_title = root.findtext("./channel/title")
    if channel_title:
        return _clean_text(channel_title)
    host = re.sub(r"^https?://", "", fallback_url).split("/", 1)[0]
    return host.replace("www.", "")


def _published_text(item: ET.Element) -> str:
    raw = item.findtext("pubDate") or item.findtext("published") or ""
    raw = _clean_text(raw)
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, IndexError):
        return raw[:32]


def _description_text(item: ET.Element) -> str:
    return (
        _clean_text(item.findtext("description"))
        or _clean_text(item.findtext("summary"))
        or _clean_text(item.findtext("{http://www.w3.org/2005/Atom}summary"))
        or _clean_text(item.findtext("{http://www.w3.org/2005/Atom}content"))
    )


def fetch_rss_feed(url: str, limit: int = 12) -> List[NewsItem]:
    xml_bytes = _download_xml(url)
    root = ET.fromstring(xml_bytes)
    source = _source_from_root(root, url)
    items = []

    rss_items = root.findall("./channel/item")
    if not rss_items:
        rss_items = root.findall(".//{http://www.w3.org/2005/Atom}entry")

    for item in rss_items[:limit]:
        title = _clean_text(item.findtext("title"))
        description = _description_text(item)
        link = _clean_text(item.findtext("link"))
        if not link:
            atom_link = item.find("{http://www.w3.org/2005/Atom}link")
            link = atom_link.attrib.get("href", "") if atom_link is not None else ""
        if not title:
            continue
        items.append(
            NewsItem(
                title=title,
                link=link,
                source=source,
                published=_published_text(item),
                score=_score_text(title, description),
                description=description,
            )
        )
    return items


def fetch_top_news(feed_urls: Iterable[str], max_items: int = 5) -> tuple[List[NewsItem], List[str]]:
    items: List[NewsItem] = []
    warnings: List[str] = []
    seen_titles = set()
    seen_topics = set()
    seen_report_headlines = set()

    for url in feed_urls:
        try:
            for item in fetch_rss_feed(url):
                key = item.title.lower()
                if key in seen_titles:
                    continue
                seen_titles.add(key)
                items.append(item)
        except Exception as exc:  # noqa: BLE001 - keep other feeds usable.
            warnings.append(f"뉴스 피드 일부를 읽지 못했습니다: {url} ({exc})")

    items = [item for item in items if item.score >= 0]
    items.sort(key=lambda item: item.score, reverse=True)
    selected: List[NewsItem] = []
    label_counts: dict[str, int] = {}
    ai_count = 0
    for item in items:
        label = korean_news_label(item)
        item_ai = label in {"AI/반도체", "AI/클라우드", "소프트웨어"}
        if label_counts.get(label, 0) >= 1:
            continue
        if item_ai and ai_count >= 3:
            continue
        topic = _specific_headline(f"{item.title} {item.description}") or item.title.lower()
        topic = re.sub(r"[^a-z0-9가-힣]+", " ", topic.lower()).strip()
        if topic in seen_topics:
            continue
        report_headline = re.sub(
            r"\s+",
            " ",
            f"{label}:{korean_news_headline(item)}".lower(),
        ).strip()
        if report_headline in seen_report_headlines:
            continue
        seen_topics.add(topic)
        seen_report_headlines.add(report_headline)
        label_counts[label] = label_counts.get(label, 0) + 1
        if item_ai:
            ai_count += 1
        selected.append(item)
        if len(selected) >= max_items:
            break
    return selected, warnings


def _combined_text(title: str | NewsItem, description: str = "") -> tuple[str, str, str]:
    if isinstance(title, NewsItem):
        return title.title, title.description, title.source
    return title, description, ""


def korean_news_label(title: str | NewsItem, description: str = "") -> str:
    title_text, description_text, _source = _combined_text(title, description)
    text = f"{title_text} {description_text}".lower()
    if any(_has_keyword(text, word) for word in ("fed", "rate", "rates", "inflation", "cpi", "pce")):
        return "금리/물가"
    if any(_has_keyword(text, word) for word in ("payroll", "payrolls", "employment", "jobs", "adp")):
        return "고용"
    if any(_has_keyword(text, word) for word in ("earnings", "revenue", "profit", "guidance")):
        return "실적"
    if any(_has_keyword(text, word) for word in ("defense", "budget", "hypersonic", "weapons")):
        return "방산"
    if any(_has_keyword(text, word) for word in ("etf", "flows", "flowing")):
        return "ETF/수급"
    if any(_has_keyword(text, word) for word in ("salesforce", "servicenow", "software", "saas")):
        return "소프트웨어"
    if any(_has_keyword(text, word) for word in ("cloud", "compute", "hyperscaler", "hyperscalers")):
        return "AI/클라우드"
    if any(_has_keyword(text, word) for word in ("ai", "chip", "semiconductor", "nvidia")):
        return "AI/반도체"
    if any(_has_keyword(text, word) for word in ("oil", "energy", "crude")):
        return "에너지"
    if any(_has_keyword(text, word) for word in ("treasury", "yield", "bond")):
        return "채권"
    if any(_has_keyword(text, word) for word in ("stock", "stocks", "market", "nasdaq", "s&p", "dow")):
        return "시장"
    return "뉴스"


COMPANY_NAMES = {
    "nvidia": "엔비디아",
    "apple": "애플",
    "microsoft": "마이크로소프트",
    "tesla": "테슬라",
    "amazon": "아마존",
    "alphabet": "알파벳",
    "google": "구글",
    "meta": "메타",
    "netflix": "넷플릭스",
    "amd": "AMD",
    "intel": "인텔",
    "broadcom": "브로드컴",
    "oracle": "오라클",
    "micron": "마이크론",
    "super micro": "슈퍼마이크로",
    "palantir": "팔란티어",
    "boeing": "보잉",
    "jpmorgan": "JP모건",
    "goldman": "골드만삭스",
}


RELATED_ASSETS = {
    "AI/반도체": "NVDA, AMD, AVGO, MU, SMH, XLK",
    "금리/물가": "QQQ, TLT, 달러, 성장주, 은행주",
    "고용": "QQQ, TLT, 달러, 경기민감주, 방어주",
    "실적": "해당 기업, 동종업계, QQQ/SPY 편입 대형주",
    "채권": "TLT, IEF, QQQ, 성장주, 부동산/유틸리티",
    "에너지": "XLE, XOM, CVX, 유가, 항공/운송주",
    "방산": "ITA, RTX, LMT, NOC, GD, 산업재",
    "ETF/수급": "SPY, QQQ, SMH, XLK, 대형 성장주",
    "소프트웨어": "CRM, NOW, MSFT, IGV, CLOU",
    "AI/클라우드": "META, MSFT, AMZN, GOOGL, NVDA",
    "시장": "SPY, QQQ, DIA, 강세/약세 섹터 ETF",
    "뉴스": "관련 대형주와 해당 섹터 ETF",
}


def _mentioned_companies(text: str) -> list[str]:
    names = []
    lower_text = text.lower()
    for keyword, korean_name in COMPANY_NAMES.items():
        if _has_company(lower_text, keyword) and korean_name not in names:
            names.append(korean_name)
    return names[:3]


def _has_company(text: str, keyword: str) -> bool:
    if " " in keyword:
        return keyword in text
    return re.search(rf"\b{re.escape(keyword)}\b", text) is not None


def _subject_text(label: str, text: str) -> str:
    companies = _mentioned_companies(text)
    if companies:
        return ", ".join(companies)
    if label == "금리/물가":
        return "연준, 금리, 물가"
    if label == "AI/반도체":
        return "AI와 반도체"
    if label == "고용":
        return "고용 지표"
    if label == "에너지":
        return "유가와 에너지"
    if label == "채권":
        return "채권금리"
    if label == "실적":
        return "기업 실적"
    if label == "방산":
        return "방산/항공우주"
    if label == "ETF/수급":
        return "ETF 자금 흐름"
    if label == "소프트웨어":
        return "소프트웨어/SaaS"
    if label == "AI/클라우드":
        return "AI 클라우드 인프라"
    if label == "시장":
        return "미국 증시"
    return "시장 주요 이슈"


def _event_text(text: str) -> str:
    lower_text = text.lower()
    if any(word in lower_text for word in ("data center", "gpu", "infrastructure", "build", "spending", "capex")):
        return "AI 인프라 투자와 수요 기대"
    if any(word in lower_text for word in ("supplier", "suppliers", "hardware", "computer-hardware")):
        return "AI 수혜가 공급망 기업으로 확산"
    if any(word in lower_text for word in ("etf", "flows", "poured money", "record pace")):
        return "ETF 자금 유입과 수급 개선"
    if any(word in lower_text for word in ("defense", "budget", "hypersonic", "weapons")):
        return "방산 예산 확대와 무기 재고 보충"
    if any(word in lower_text for word in ("earnings", "revenue", "profit", "guidance", "results")):
        return "실적과 향후 전망"
    if any(word in lower_text for word in ("fed", "rate", "rates", "inflation", "cpi", "pce")):
        return "연준 정책과 금리 기대"
    if any(word in lower_text for word in ("jobs", "payroll", "labor", "employment")):
        return "고용 지표와 경기 판단"
    if any(word in lower_text for word in ("treasury", "yield", "bond")):
        return "채권금리 움직임"
    if any(word in lower_text for word in ("oil", "crude", "opec", "energy")):
        return "유가와 에너지 수급"
    if any(word in lower_text for word in ("tariff", "trade", "china", "export")):
        return "무역정책과 공급망 변수"
    if any(word in lower_text for word in ("stock", "stocks", "market", "nasdaq", "s&p", "dow")):
        return "증시 분위기와 수급"
    return "투자심리에 영향을 줄 수 있는 변화"


def _specific_headline(text: str) -> str:
    lower_text = text.lower()
    if "futures fall" in lower_text and ("chip stocks" in lower_text or "semiconductor" in lower_text):
        return "지수 선물은 약하지만 2분기 반도체주는 강하게 오른 엇갈린 흐름"
    if ("nasdaq slips" in lower_text or "nasdaq falls" in lower_text) and (
        "micron falls" in lower_text or "chip firms tumble" in lower_text
    ):
        return "나스닥이 강한 분기 상승 뒤 쉬어가고 마이크론 등 반도체가 차익실현을 받는 흐름"
    if "warsh" in lower_text and ("fed" in lower_text or "interest-rate" in lower_text):
        return "연준 수장이 향후 금리 경로에 대한 힌트를 주지 않아 금리 불확실성이 남은 상황"
    if "nasdaq-100" in lower_text and ("just 10 stocks" in lower_text or "10 stocks" in lower_text):
        return "나스닥100 상승분이 소수 대형주에 집중돼 시장 폭이 좁다는 신호"
    if "meta" in lower_text and ("cloud" in lower_text or "compute" in lower_text):
        return "메타가 남는 AI 컴퓨팅을 클라우드로 판매해 인프라 투자 부담을 수익화하려는 움직임"
    if ("servicenow" in lower_text or "salesforce" in lower_text) and (
        "buys" in lower_text or "valuation" in lower_text or "armageddon" in lower_text
    ):
        return "소프트웨어주가 AI 위협으로 과도하게 눌렸다는 저가 매수 분석"
    if "private payrolls" in lower_text or "adp" in lower_text:
        return "민간 고용이 예상보다 약해 경기 둔화와 금리 인하 기대가 동시에 커질 수 있는 지표"
    if "energy stocks" in lower_text and "dividend etf" in lower_text:
        return "배당 ETF 안에서 에너지 비중이 커져 방어적 현금흐름과 유가 민감도가 같이 커진 상황"
    if "global etf" in lower_text and "bear market" in lower_text:
        return "중국 등 약세장 ETF에 역발상 자금이 들어가는 고위험 베팅"
    if "bottlenecks" in lower_text and ("chip-stock rally" in lower_text or "hyperscalers" in lower_text):
        return "AI 인프라 병목과 빅테크 지출 지속이 반도체 랠리를 떠받친다는 분석"
    if "microsoft" in lower_text and "layoffs" in lower_text and "ai" in lower_text:
        return "마이크로소프트가 AI 투자를 늘리면서도 인력 감축을 준비하는 비용 조절 신호"
    if "nike" in lower_text and "alcoa" in lower_text and "amd" in lower_text and "micron" in lower_text:
        return "나이키, 알코아, AMD, 마이크론 등 개별주가 시장 흐름을 설명하는 장세"
    if "record chip rally" in lower_text or (
        "micron" in lower_text and "intel" in lower_text and "amd" in lower_text
    ):
        return "엔비디아 밖으로 반도체 랠리가 확산되며 Micron, Intel, AMD까지 수급이 붙는 흐름"
    if "best-performing stocks" in lower_text and (
        "semiconductor" in lower_text or "hardware" in lower_text
    ):
        return "상반기 S&P 500 주도주가 반도체와 컴퓨터 하드웨어 쪽에 집중"
    if "ai trade has shifted" in lower_text or (
        "rewarding" in lower_text and "supplying" in lower_text
    ):
        return "AI 투자 중심이 빅테크 플랫폼에서 장비·부품 공급망 기업으로 이동"
    if "etf" in lower_text and (
        "record pace" in lower_text or "flowing" in lower_text or "poured money" in lower_text
    ):
        return "ETF 자금이 기록적인 속도로 유입되며 AI 관련 주식 선호가 이어짐"
    if "defense budget" in lower_text or "hypersonic" in lower_text or "weapons stocks" in lower_text:
        return "미국 방산 예산 확대와 무기 재고 보충 경쟁이 방산·산업재 수요를 자극"
    return ""


def korean_news_headline(title: str | NewsItem, description: str = "") -> str:
    title_text, description_text, _source = _combined_text(title, description)
    text = f"{title_text} {description_text}"
    specific = _specific_headline(text)
    if specific:
        return specific
    label = korean_news_label(title_text, description_text)
    subject = _subject_text(label, text)
    event = _event_text(text)
    return f"{subject}: {event}"


def korean_news_summary(title: str | NewsItem, description: str = "") -> str:
    title_text, description_text, _source = _combined_text(title, description)
    text = f"{title_text} {description_text}"
    lower_text = text.lower()
    label = korean_news_label(title_text, description_text)
    subject = _subject_text(label, text)
    event = _event_text(text)
    specific = _specific_headline(text)

    if specific and label == "AI/반도체":
        return f"핵심은 {specific}이라는 점입니다. AI 테마가 소수 대장주에서 공급망 전반으로 넓어지는지 확인하는 뉴스입니다."
    if specific and label == "ETF/수급":
        return f"핵심은 {specific}이라는 점입니다. 실제 자금 유입이 이어지면 테마의 지속성을 뒷받침하지만, 쏠림이 커지면 변동성도 커질 수 있습니다."
    if specific and label == "방산":
        return f"핵심은 {specific}한다는 점입니다. 정책 예산이 실적 기대와 수주 모멘텀으로 이어지는지 봐야 합니다."
    if label == "AI/반도체":
        return f"{subject} 관련 {event} 뉴스입니다. 반도체, 전력, 데이터센터 투자심리에 영향을 줄 수 있습니다."
    if label == "금리/물가":
        return f"{subject} 관련 {event} 뉴스입니다. 성장주 밸류에이션과 달러/채권금리 반응을 같이 봐야 합니다."
    if label == "고용":
        return f"{subject} 관련 {event} 뉴스입니다. 금리 인하 기대와 경기 둔화 우려가 동시에 움직일 수 있습니다."
    if label == "실적":
        return f"{subject} 관련 {event} 뉴스입니다. 매출 성장률, 마진, 가이던스가 주가 반응의 핵심입니다."
    if label == "채권":
        return f"{subject} 관련 {event} 뉴스입니다. 금리 상승은 성장주에 부담, 하락은 위험자산에 우호적일 수 있습니다."
    if label == "에너지":
        return f"{subject} 관련 {event} 뉴스입니다. 에너지주와 물가 기대에 이어질 수 있습니다."
    if label == "방산":
        return f"{subject} 관련 {event} 뉴스입니다. 수주, 정부 예산, 산업재 섹터의 상대강도를 함께 봐야 합니다."
    if label == "ETF/수급":
        return f"{subject} 관련 {event} 뉴스입니다. 테마 자금 유입이 이어지는지, 과열 신호가 커지는지 확인해야 합니다."
    if label == "소프트웨어":
        return f"{subject} 관련 {event} 뉴스입니다. AI 우려가 밸류에이션 할인인지, 실적 훼손인지 구분해야 합니다."
    if label == "AI/클라우드":
        return f"{subject} 관련 {event} 뉴스입니다. AI 인프라 비용이 매출화되는지와 마진 부담을 같이 봐야 합니다."
    if label == "시장":
        return f"{subject} 관련 {event} 뉴스입니다. 지수 방향보다 어떤 업종에 돈이 몰렸는지 확인해야 합니다."
    if any(_has_keyword(lower_text, word) for word in ("tesla", "apple", "microsoft", "amazon", "alphabet", "meta")):
        return f"{subject} 관련 개별 종목 뉴스입니다. 대형주 비중이 커 지수 영향도 함께 봐야 합니다."
    return f"{subject} 관련 뉴스입니다. 단기 매매보다 시장심리와 관련 업종 반응을 확인하는 용도로 보세요."


def korean_news_plain_explanation(title: str | NewsItem, description: str = "") -> str:
    title_text, description_text, _source = _combined_text(title, description)
    text = f"{title_text} {description_text}"
    label = korean_news_label(title_text, description_text)
    subject = _subject_text(label, text)
    event = _event_text(text)
    headline = korean_news_headline(title_text, description_text)
    return f"{headline}입니다. 쉽게 말해 {subject} 쪽에서 {event}가 투자심리에 영향을 주는 뉴스입니다."


def korean_news_why_it_matters(title: str | NewsItem, description: str = "") -> str:
    title_text, description_text, _source = _combined_text(title, description)
    label = korean_news_label(title_text, description_text)
    if label == "AI/반도체":
        return "AI/반도체는 최근 지수 주도력이 큰 테마라, 이 뉴스가 좋게 해석되면 나스닥과 성장주 심리에 바로 연결될 수 있습니다."
    if label == "AI/클라우드":
        return "AI 인프라 지출은 반도체 수요를 만들지만 비용 부담도 큽니다. 매출화 신호인지, 지출 부담 신호인지 구분해야 합니다."
    if label == "소프트웨어":
        return "소프트웨어주는 AI가 위협인지 생산성 개선인지에 따라 밸류에이션이 크게 달라질 수 있습니다."
    if label == "금리/물가":
        return "금리와 물가는 주식의 할인율을 바꿉니다. 금리가 오르면 성장주가 눌리고, 금리가 내려가면 성장주 반등 근거가 생깁니다."
    if label == "고용":
        return "고용은 연준의 금리 판단과 경기 판단을 동시에 흔듭니다. 약한 고용은 금리 인하 기대와 경기 둔화 우려를 같이 만듭니다."
    if label == "실적":
        return "실적 뉴스는 해당 기업뿐 아니라 동종 업계의 매출 성장률과 마진 기대를 다시 평가하게 만듭니다."
    if label == "채권":
        return "채권금리 방향은 성장주, 배당주, 부동산/유틸리티 같은 금리 민감 섹터의 상대 강도를 바꿉니다."
    if label == "에너지":
        return "유가는 에너지 기업에는 호재가 될 수 있지만, 물가와 소비 비용에는 부담이 될 수 있어 시장 해석이 갈릴 수 있습니다."
    if label == "방산":
        return "방산 뉴스는 정책과 예산이 실제 수주로 이어질 수 있는지에 따라 산업재 섹터 모멘텀이 생깁니다."
    if label == "ETF/수급":
        return "ETF 자금 흐름은 실제 돈이 어디로 이동하는지 보여줍니다. 테마가 지속되는지, 과열되는지 판단하는 단서입니다."
    return "이 뉴스는 단독 매매 신호라기보다 시장이 어떤 이야기에 반응하는지 확인하는 재료입니다."


def korean_news_thinking_frame(title: str | NewsItem, description: str = "") -> str:
    title_text, description_text, _source = _combined_text(title, description)
    label = korean_news_label(title_text, description_text)
    sentiment, _reason = korean_news_sentiment(title_text, description_text)
    if sentiment in {"긍정", "중립+"}:
        tone = "우호적인 뉴스지만, 이미 가격이 많이 오른 뒤라면 추격보다 다음날 거래량과 상대강도 확인이 먼저입니다."
    elif sentiment in {"부정", "중립-"}:
        tone = "부담스러운 뉴스라면 바로 매수하기보다 관련 ETF가 지수보다 약해지는지 확인하는 게 우선입니다."
    elif sentiment == "혼재":
        tone = "좋은 점과 나쁜 점이 같이 있으므로 지수보다 섹터 내부 승자와 패자를 나눠 보는 뉴스입니다."
    else:
        tone = "방향성이 강하지 않으므로 가격 반응이 확인될 때만 판단에 반영하는 편이 낫습니다."

    if label in {"AI/반도체", "AI/클라우드", "소프트웨어"}:
        return f"{tone} 특히 AI 테마가 대형주 한두 개가 아니라 관련 공급망으로 넓어지는지 봐야 합니다."
    if label in {"금리/물가", "고용", "채권"}:
        return f"{tone} 핵심은 뉴스 자체보다 10년물 금리, 달러, 나스닥이 같은 방향으로 반응하는지입니다."
    if label in {"방산", "에너지", "ETF/수급"}:
        return f"{tone} 관련 섹터 ETF가 시장보다 강한지, 하루짜리 뉴스로 끝나는지 구분해야 합니다."
    return tone


def korean_news_scenario(title: str | NewsItem, description: str = "") -> tuple[str, str]:
    title_text, description_text, _source = _combined_text(title, description)
    label = korean_news_label(title_text, description_text)
    if label == "AI/반도체":
        return (
            "반도체와 전력/장비/메모리까지 같이 오르면 AI 투자 사이클 지속 신호입니다.",
            "대형 반도체만 오르고 주변 공급망이 못 따라오면 쏠림 또는 차익실현 위험입니다.",
        )
    if label == "AI/클라우드":
        return (
            "클라우드 매출과 반도체 수요가 같이 강해지면 AI 지출이 성과로 바뀌는 신호입니다.",
            "매출보다 비용 증가가 부각되면 빅테크 마진 부담으로 해석될 수 있습니다.",
        )
    if label == "소프트웨어":
        return (
            "SaaS 종목이 나스닥보다 강하면 AI 우려가 과도했다는 재평가가 나올 수 있습니다.",
            "실적 전망이 나빠지면 저가 매수 논리보다 구조적 둔화 우려가 커집니다.",
        )
    if label in {"금리/물가", "고용", "채권"}:
        return (
            "금리 하락과 나스닥 강세가 같이 나오면 성장주에는 우호적인 조합입니다.",
            "금리 상승, 달러 강세, 나스닥 약세가 같이 나오면 위험자산 부담 신호입니다.",
        )
    if label == "방산":
        return (
            "방산주와 산업재 ETF가 지수보다 강하면 정책 모멘텀이 가격에 반영되는 신호입니다.",
            "뉴스는 좋아도 주가가 반응하지 않으면 이미 기대가 반영됐을 수 있습니다.",
        )
    if label == "에너지":
        return (
            "유가 상승과 에너지주 강세가 같이 나오면 섹터 모멘텀은 유지됩니다.",
            "유가 상승이 금리와 물가 우려를 키우면 시장 전체에는 부담이 될 수 있습니다.",
        )
    if label == "ETF/수급":
        return (
            "자금 유입과 가격 상승이 같이 나오면 수급이 테마를 밀어주는 신호입니다.",
            "자금 유입에도 가격이 밀리면 과열 해소나 매물 출회 가능성을 봐야 합니다.",
        )
    return (
        "관련 섹터가 지수보다 강하면 투자자들이 뉴스에 반응하고 있다는 뜻입니다.",
        "관련 종목이 반응하지 않으면 단기 재료로만 끝날 가능성이 큽니다.",
    )


def korean_news_next_signals(title: str | NewsItem, description: str = "") -> list[str]:
    signals = korean_news_checkpoints(title, description)
    title_text, description_text, _source = _combined_text(title, description)
    label = korean_news_label(title_text, description_text)
    if label in {"AI/반도체", "AI/클라우드", "소프트웨어"}:
        signals.append("SMH/XLK가 SPY보다 강한지 확인")
    elif label in {"금리/물가", "고용", "채권"}:
        signals.append("10년물 금리와 QQQ 방향이 같은지 확인")
    elif label == "방산":
        signals.append("ITA와 주요 방산주가 산업재보다 강한지 확인")
    elif label == "에너지":
        signals.append("유가와 XLE가 같은 방향인지 확인")
    elif label == "ETF/수급":
        signals.append("거래량 증가가 가격 상승과 같이 나오는지 확인")
    else:
        signals.append("관련 ETF가 다음 거래일에도 지수보다 강한지 확인")
    return signals[:4]


def korean_news_sentiment(title: str | NewsItem, description: str = "") -> tuple[str, str]:
    title_text, description_text, _source = _combined_text(title, description)
    text = f"{title_text} {description_text}"
    lower_text = text.lower()
    label = korean_news_label(title_text, description_text)

    positive_words = (
        "rally",
        "record",
        "adds",
        "expanded",
        "poured",
        "growth",
        "strong",
        "beat",
        "raises",
        "upgrade",
        "inflow",
        "flows",
        "rewarding",
        "leading",
        "best-performing",
        "demand",
    )
    negative_words = (
        "slump",
        "fall",
        "drops",
        "warning",
        "miss",
        "cuts",
        "downgrade",
        "risk",
        "fear",
        "tariff",
        "depleted",
        "battle",
        "weak",
        "selloff",
        "loss",
    )

    positive_score = sum(1 for word in positive_words if word in lower_text)
    negative_score = sum(1 for word in negative_words if word in lower_text)

    if "futures fall" in lower_text and ("chip stocks" in lower_text or "semiconductor" in lower_text):
        return "혼재", "지수 단기 흐름은 약하지만 반도체 주도주는 강해 업종별 차별화가 핵심입니다."
    if ("nasdaq slips" in lower_text or "nasdaq falls" in lower_text) and (
        "micron falls" in lower_text or "chip firms tumble" in lower_text
    ):
        return "혼재", "반도체 장기 테마는 살아 있어도 단기 과열 해소와 차익실현은 경계해야 합니다."
    if "warsh" in lower_text and ("fed" in lower_text or "interest-rate" in lower_text):
        return "중립-", "금리 경로 힌트가 적으면 시장은 채권금리와 물가 지표에 더 민감해집니다."
    if "nasdaq-100" in lower_text and ("just 10 stocks" in lower_text or "10 stocks" in lower_text):
        return "혼재", "대형주 주도는 지수를 밀어 올리지만 시장 폭이 좁으면 조정에 취약합니다."
    if "meta" in lower_text and ("cloud" in lower_text or "compute" in lower_text):
        return "긍정", "AI 인프라 비용을 외부 매출로 바꾸려는 시도라 투자 부담 완화 신호입니다."
    if ("servicenow" in lower_text or "salesforce" in lower_text) and (
        "buys" in lower_text or "valuation" in lower_text or "armageddon" in lower_text
    ):
        return "긍정", "AI 우려로 눌린 소프트웨어주에 밸류에이션 반등 논리가 붙는 뉴스입니다."
    if "private payrolls" in lower_text or "adp" in lower_text:
        return "혼재", "고용 둔화는 금리 인하 기대에는 우호적이지만 경기 둔화 우려도 키울 수 있습니다."
    if "energy stocks" in lower_text and "dividend etf" in lower_text:
        return "혼재", "배당 매력은 있지만 에너지 비중이 커지면 유가 변동성에 더 노출됩니다."
    if "global etf" in lower_text and "bear market" in lower_text:
        return "중립-", "약세장 ETF로 들어가는 역발상 자금은 반등 기대와 손실 위험이 같이 큽니다."
    if "bottlenecks" in lower_text and ("chip-stock rally" in lower_text or "hyperscalers" in lower_text):
        return "긍정", "AI 인프라 병목과 빅테크 지출 지속은 반도체 수요가 이어질 수 있다는 근거입니다."
    if "microsoft" in lower_text and "layoffs" in lower_text and "ai" in lower_text:
        return "혼재", "AI 투자는 성장 기대를 주지만 감원은 비용 부담과 조직 재편 신호로 봐야 합니다."
    if "nike" in lower_text and "alcoa" in lower_text and "amd" in lower_text and "micron" in lower_text:
        return "중립", "시장 전체보다 개별 종목 이슈가 주가를 갈라놓는 장세라는 뜻입니다."
    if label == "AI/반도체" and positive_score >= negative_score:
        return "긍정", "AI 수요와 반도체 공급망으로 돈이 확산되는 내용이라 성장주 심리에 우호적입니다."
    if label == "ETF/수급" and positive_score >= negative_score:
        return "긍정", "실제 자금 유입은 테마 지속성을 뒷받침하지만 쏠림 과열은 같이 봐야 합니다."
    if label == "방산":
        return "중립+", "예산 확대는 방산주에 우호적이나 정책 뉴스라 실제 수주 확인이 필요합니다."
    if label == "금리/물가":
        if negative_score > positive_score or any(word in lower_text for word in ("inflation", "higher rate", "rates rise")):
            return "부정", "금리와 물가 부담은 성장주 밸류에이션을 누를 수 있습니다."
        return "중립", "연준 기대 변화는 방향보다 채권금리와 달러 반응이 더 중요합니다."
    if label == "고용":
        return "혼재", "고용 지표는 약하면 금리 인하 기대, 강하면 금리 부담으로 해석될 수 있습니다."
    if label == "채권":
        return "중립", "채권금리 방향에 따라 성장주와 방어주의 해석이 달라집니다."
    if label == "에너지":
        return "혼재", "에너지주에는 우호적일 수 있지만 유가 상승은 물가 부담으로도 이어질 수 있습니다."
    if positive_score > negative_score:
        return "긍정", "수급이나 성장 기대를 높이는 단서가 더 많습니다."
    if negative_score > positive_score:
        return "부정", "불확실성이나 비용 부담을 키우는 단서가 더 많습니다."
    return "중립", "방향성이 강하지 않아 관련 종목의 실제 가격 반응을 확인해야 합니다."


def korean_news_importance(title: str | NewsItem, description: str = "") -> tuple[str, str]:
    title_text, description_text, _source = _combined_text(title, description)
    text = f"{title_text} {description_text}".lower()
    label = korean_news_label(title_text, description_text)
    high_impact_labels = {"금리/물가", "고용", "실적", "AI/반도체"}
    if label in high_impact_labels:
        return "A급", "지수 또는 주도 섹터에 직접 영향을 줄 수 있습니다."
    if label in {"채권", "에너지", "방산", "AI/클라우드", "ETF/수급"}:
        return "B급", "관련 섹터와 주요 종목에 영향을 줄 수 있습니다."
    if any(word in text for word in ("apple", "microsoft", "nvidia", "tesla", "amazon", "meta", "alphabet")):
        return "B급", "대형주라 지수 심리와 동종 업종에 영향을 줄 수 있습니다."
    return "C급", "가격 반응이 확인될 때만 투자 판단에 반영합니다."


def korean_news_checkpoints(title: str | NewsItem, description: str = "") -> list[str]:
    title_text, description_text, _source = _combined_text(title, description)
    text = f"{title_text} {description_text}"
    lower_text = text.lower()
    label = korean_news_label(title_text, description_text)
    checkpoints: list[str] = []

    if label == "AI/반도체":
        checkpoints.extend(
            [
                "반도체 랠리가 특정 대형주에만 머무는지, 장비/메모리/전력 인프라까지 확산되는지 확인",
                "기술주 강세가 금리 상승에도 버티면 위험선호 유지 신호로 해석",
            ]
        )
    elif label == "금리/물가":
        checkpoints.extend(
            [
                "10년물 금리와 달러가 같이 오르면 성장주에는 부담",
                "금리 하락과 나스닥 강세가 같이 나오면 성장주 반등의 질이 좋아짐",
            ]
        )
    elif label == "고용":
        checkpoints.extend(
            [
                "고용 둔화에 금리가 내려가고 나스닥이 버티면 성장주에는 우호적",
                "고용 둔화에 경기민감주가 같이 밀리면 경기침체 우려로 해석",
            ]
        )
    elif label == "실적":
        checkpoints.extend(
            [
                "매출보다 마진과 다음 분기 가이던스가 주가 방향을 좌우하는지 확인",
                "동종업계 주가가 같이 움직이면 업종 전체 재평가 가능성",
            ]
        )
    elif label == "채권":
        checkpoints.extend(
            [
                "채권금리 급등은 고PER 성장주와 부동산/유틸리티에 부담",
                "금리 하락이 경기둔화 우려 때문인지, 인플레 완화 때문인지 구분",
            ]
        )
    elif label == "에너지":
        checkpoints.extend(
            [
                "유가 상승이 에너지주에는 호재지만 물가 기대를 자극하는지 확인",
                "항공, 운송, 소비재처럼 비용 부담을 받는 업종 반응도 같이 점검",
            ]
        )
    elif label == "방산":
        checkpoints.extend(
            [
                "방산 예산 뉴스가 실제 수주와 매출 가이던스 상향으로 연결되는지 확인",
                "산업재 ETF와 주요 방산주가 지수보다 강하면 정책 모멘텀 지속 신호",
            ]
        )
    elif label == "ETF/수급":
        checkpoints.extend(
            [
                "ETF 자금 유입이 AI/반도체에 집중되는지, 시장 전반으로 넓어지는지 확인",
                "강한 유입 뒤 가격이 밀리면 과열 해소 구간일 수 있어 거래량 확인",
            ]
        )
    elif label == "소프트웨어":
        checkpoints.extend(
            [
                "AI 우려로 눌린 밸류에이션이 반등하는지, 실적 전망이 같이 개선되는지 확인",
                "CRM, NOW 같은 SaaS 종목이 나스닥보다 강하면 소프트웨어 반등 신호",
            ]
        )
    elif label == "AI/클라우드":
        checkpoints.extend(
            [
                "AI 인프라 지출이 매출로 회수되는지, 마진 부담으로 남는지 확인",
                "클라우드 대형주와 반도체가 같이 오르면 AI 투자 사이클 지속 신호",
            ]
        )
    else:
        checkpoints.extend(
            [
                "지수 방향보다 거래대금이 어느 섹터로 몰렸는지 확인",
                "강세 업종이 다음 거래일에도 이어지는지, 하루짜리 반등인지 구분",
            ]
        )

    if any(_has_keyword(lower_text, word) for word in ("tariff", "china", "export", "trade")):
        checkpoints.append("중국/수출 규제 이슈는 반도체와 대형 기술주의 밸류에이션 할인 요인인지 확인")
    if label != "방산" and any(word in lower_text for word in ("jobs", "payroll", "employment", "labor")):
        checkpoints.append("고용이 너무 강하면 금리 부담, 너무 약하면 경기둔화 우려로 연결될 수 있음")
    if any(word in lower_text for word in ("data center", "gpu", "infrastructure")):
        checkpoints.append("데이터센터 투자 뉴스는 반도체뿐 아니라 전력, 냉각, 네트워크 장비까지 파급 여부 확인")

    return checkpoints[:3]


def korean_news_related(title: str | NewsItem, description: str = "") -> str:
    title_text, description_text, _source = _combined_text(title, description)
    text = f"{title_text} {description_text}"
    label = korean_news_label(title_text, description_text)
    companies = _mentioned_companies(text)
    related = RELATED_ASSETS.get(label, RELATED_ASSETS["뉴스"])
    if companies:
        return f"{', '.join(companies)} / {related}"
    return related
