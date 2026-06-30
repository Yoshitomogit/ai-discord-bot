"""
fetchers_shindanshi.py
中小企業診断士向けニュースを収集するモジュール。

対応ソース:
  - Google News RSS (認証不要)
  - J-SMECA RSS (中小企業診断協会)
  - 中小企業庁 新着情報 (HTML スクレイピング)
"""

import asyncio
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup


@dataclass
class Article:
    title: str
    url: str
    source: str
    published: datetime
    category: str
    summary: str = ""


CATEGORIES = {
    "seisaku": {
        "label": "国の施策",
        "color": 0x1E90FF,
        "emoji": "🔵",
        "keywords": ["中小企業政策", "経産省", "中小企業庁", "法改正", "施策"],
    },
    "hojyokin": {
        "label": "補助金・助成金",
        "color": 0x28A745,
        "emoji": "🟢",
        "keywords": [
            "補助金", "助成金", "ものづくり補助金", "IT導入補助金",
            "事業再構築", "持続化補助金",
        ],
    },
    "kinyu": {
        "label": "金融・資金繰り",
        "color": 0xFFC107,
        "emoji": "🟡",
        "keywords": [
            "日本政策金融公庫", "信用保証", "セーフティネット", "融資", "資金繰り",
        ],
    },
    "keisho": {
        "label": "事業承継・DX",
        "color": 0xFF6B35,
        "emoji": "🟠",
        "keywords": ["事業承継", "M&A", "DX", "デジタル化", "後継者"],
    },
    "roumu": {
        "label": "人事・労務・税制",
        "color": 0xDC3545,
        "emoji": "🔴",
        "keywords": [
            "最低賃金", "雇用調整", "労働基準", "税制改正", "社会保険",
        ],
    },
    "shiken": {
        "label": "診断士試験・資格",
        "color": 0x6F42C1,
        "emoji": "🟣",
        "keywords": [
            "中小企業診断士", "診断士試験", "1次試験", "2次試験",
            "実務補習", "中小企業診断協会",
        ],
    },
}

GOOGLE_NEWS_QUERIES = {
    "seisaku":  "中小企業施策",
    "hojyokin": "補助金 中小企業",
    "kinyu":    "中小企業 融資",
    "keisho":   "事業承継 中小企業",
    "roumu":    "最低賃金 中小企業",
    "shiken":   "中小企業診断士",
}


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", unescape(text)).strip()


def _parse_dt(s: str) -> datetime:
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y/%m/%d",
        "%Y.%m.%d",
    ):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(tz=timezone.utc)


def detect_category(text: str) -> Optional[str]:
    for cat_key, cat_info in CATEGORIES.items():
        if any(kw in text for kw in cat_info["keywords"]):
            return cat_key
    return None


# ─── Google News RSS ─────────────────────────────────────────────────────

async def fetch_google_news(session: aiohttp.ClientSession) -> list[Article]:
    results = []
    headers = {"User-Agent": "Mozilla/5.0 (Shindanshi-News-Bot)"}

    for cat_key, query in GOOGLE_NEWS_QUERIES.items():
        encoded = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP:ja"
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    continue
                text = await resp.text()
                root = ET.fromstring(text)
                items = root.findall(".//item")
                for item in items[:5]:
                    title_el = item.find("title")
                    link_el = item.find("link")
                    date_el = item.find("pubDate")
                    desc_el = item.find("description")

                    title = (title_el.text or "").strip() if title_el is not None else ""
                    link = (link_el.text or "").strip() if link_el is not None else ""
                    date_str = (date_el.text or "") if date_el is not None else ""
                    desc = _strip_html(desc_el.text or "") if desc_el is not None else ""

                    if not title or not link:
                        continue

                    results.append(Article(
                        title=title[:200],
                        url=link,
                        source="Google News",
                        published=_parse_dt(date_str) if date_str else datetime.now(tz=timezone.utc),
                        category=cat_key,
                        summary=desc[:50] if desc else "",
                    ))
        except Exception:
            continue
        await asyncio.sleep(0.3)

    return results


# ─── J-SMECA RSS ─────────────────────────────────────────────────────────

async def fetch_jsmeca(session: aiohttp.ClientSession) -> list[Article]:
    results = []
    url = "https://www.j-smeca.jp/contents/rss/rss.xml"
    headers = {"User-Agent": "Mozilla/5.0 (Shindanshi-News-Bot)"}

    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return []
            text = await resp.text()
            root = ET.fromstring(text)
            items = root.findall(".//item")
            for item in items[:10]:
                title_el = item.find("title")
                link_el = item.find("link")
                date_el = item.find("pubDate") or item.find("dc:date")
                desc_el = item.find("description")

                title = (title_el.text or "").strip() if title_el is not None else ""
                link = (link_el.text or "").strip() if link_el is not None else ""
                date_str = (date_el.text or "") if date_el is not None else ""
                desc = _strip_html(desc_el.text or "") if desc_el is not None else ""

                if not title or not link:
                    continue

                cat = detect_category(title + desc) or "shiken"
                results.append(Article(
                    title=title[:200],
                    url=link,
                    source="J-SMECA",
                    published=_parse_dt(date_str) if date_str else datetime.now(tz=timezone.utc),
                    category=cat,
                    summary=desc[:50] if desc else "",
                ))
    except Exception:
        pass

    return results


# ─── 中小企業庁 新着情報 ─────────────────────────────────────────────────

async def fetch_chusho(session: aiohttp.ClientSession) -> list[Article]:
    results = []
    url = "https://www.chusho.meti.go.jp/"
    headers = {"User-Agent": "Mozilla/5.0 (Shindanshi-News-Bot)"}

    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return []
            html = await resp.text()
            soup = BeautifulSoup(html, "lxml")

            news_items = soup.select("div.whatsnew-firing ul li, div.news_firing ul li, ul.firing li")
            if not news_items:
                news_items = soup.select("div#whatsnew li, div.whatsnew li")

            for item in news_items[:15]:
                a_tag = item.find("a")
                if not a_tag:
                    continue

                title = a_tag.get_text(strip=True)[:200]
                link = a_tag.get("href", "")
                if link and not link.startswith("http"):
                    link = "https://www.chusho.meti.go.jp" + ("" if link.startswith("/") else "/") + link

                if not title or not link:
                    continue

                date_text = item.get_text()
                date_match = re.search(r"(\d{4})[./年](\d{1,2})[./月](\d{1,2})", date_text)
                pub_date = datetime.now(tz=timezone.utc)
                if date_match:
                    try:
                        pub_date = datetime(
                            int(date_match.group(1)),
                            int(date_match.group(2)),
                            int(date_match.group(3)),
                            tzinfo=timezone.utc,
                        )
                    except ValueError:
                        pass

                cat = detect_category(title) or "seisaku"
                results.append(Article(
                    title=title,
                    url=link,
                    source="中小企業庁",
                    published=pub_date,
                    category=cat,
                    summary="",
                ))
    except Exception:
        pass

    return results


# ─── メイン収集関数 ──────────────────────────────────────────────────────

async def fetch_all() -> dict[str, list[Article]]:
    async with aiohttp.ClientSession() as session:
        google, jsmeca, chusho = await asyncio.gather(
            fetch_google_news(session),
            fetch_jsmeca(session),
            fetch_chusho(session),
        )

    all_articles = google + jsmeca + chusho

    categorized: dict[str, list[Article]] = {cat: [] for cat in CATEGORIES}
    seen_urls: set[str] = set()
    for article in sorted(all_articles, key=lambda a: a.published, reverse=True):
        if article.url in seen_urls:
            continue
        seen_urls.add(article.url)
        categorized[article.category].append(article)

    return {cat: articles[:2] for cat, articles in categorized.items()}
