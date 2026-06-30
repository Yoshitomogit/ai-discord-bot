"""
fetchers.py
各ソースから Gemini / ChatGPT / Copilot / Claude / Cursor の最新情報を取得するモジュール。

対応ソース:
  - Reddit JSON API  (認証不要)
  - Hacker News Algolia Search API (認証不要)
  - 公式ブログ RSS / Web スクレイピング
  - Twitter/X API v2  (Bearer Token が必要)
"""

import asyncio
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

# ─── データ型 ──────────────────────────────────────────────────────────────

@dataclass
class Article:
    title: str
    url: str
    source: str          # "Reddit", "HackerNews", "Blog", "Twitter"
    score: int           # upvotes / points / like_count
    published: datetime
    tag: str             # "gemini" | "chatgpt" | "copilot" | "claude" | "cursor"
    summary: str = ""


# ─── 定数 ─────────────────────────────────────────────────────────────────

TAGS = {
    "gemini":  ["gemini", "google ai", "google deepmind", "bard"],
    "chatgpt": ["chatgpt", "openai", "gpt-4", "gpt4", "o1", "o3", "o4"],
    "copilot": ["copilot", "github copilot", "microsoft copilot", "bing chat"],
    "claude":  ["claude", "anthropic", "claude 3", "claude 4", "sonnet", "haiku", "opus"],
    "cursor":  ["cursor", "cursor ide", "cursor editor", "cursor ai"],
}

REDDIT_SUBS = [
    "ChatGPT", "ClaudeAI", "LocalLLaMA", "artificial",
    "MachineLearning", "technology", "singularity",
]

# 公式ブログ RSS フィード
RSS_FEEDS = [
    ("OpenAI",     "https://openai.com/blog/rss/"),
    ("Anthropic",  "https://www.anthropic.com/rss.xml"),
    ("Google AI",  "https://blog.google/technology/ai/rss/"),
    ("GitHub",     "https://github.blog/feed/"),
    ("Cursor",     "https://cursor.com/blog/rss"),
]


# ─── ユーティリティ ────────────────────────────────────────────────────────

def detect_tag(text: str) -> Optional[str]:
    """テキストに含まれるキーワードからタグを返す。複数マッチは最初のもの。"""
    lower = text.lower()
    for tag, keywords in TAGS.items():
        if any(kw in lower for kw in keywords):
            return tag
    return None


def _parse_dt(s: str) -> datetime:
    """ISO 8601 / RFC 2822 / Unix timestamp を datetime(UTC) に変換。"""
    if isinstance(s, (int, float)):
        return datetime.fromtimestamp(s, tz=timezone.utc)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(tz=timezone.utc)


# ─── Reddit ───────────────────────────────────────────────────────────────

async def fetch_reddit(session: aiohttp.ClientSession, limit: int = 5) -> list[Article]:
    """Reddit の各サブレディットのホットポストからAI関連を収集。"""
    results = []
    headers = {"User-Agent": "AI-Discord-Bot/1.0 (by u/ai_news_bot)"}

    for sub in REDDIT_SUBS:
        url = f"https://www.reddit.com/r/{sub}/hot.json?limit=25"
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
                posts = data.get("data", {}).get("children", [])
                for post in posts:
                    p = post["data"]
                    text = f"{p.get('title', '')} {p.get('selftext', '')}"
                    tag = detect_tag(text)
                    if not tag:
                        continue
                    results.append(Article(
                        title=p["title"][:200],
                        url=f"https://reddit.com{p['permalink']}",
                        source="Reddit",
                        score=p.get("score", 0),
                        published=_parse_dt(p.get("created_utc", 0)),
                        tag=tag,
                    ))
        except Exception:
            continue
        await asyncio.sleep(0.5)  # Reddit レート制限対策

    # スコア降順で上位 limit 件
    results.sort(key=lambda a: a.score, reverse=True)
    return results[:limit * len(TAGS)]


# ─── Hacker News (Algolia) ────────────────────────────────────────────────

async def fetch_hackernews(session: aiohttp.ClientSession, limit: int = 5) -> list[Article]:
    """Algolia HN Search API でキーワード検索。"""
    results = []
    search_terms = ["Gemini AI", "ChatGPT", "GitHub Copilot", "Claude Anthropic", "Cursor IDE"]

    for term in search_terms:
        url = (
            "https://hn.algolia.com/api/v1/search"
            f"?query={aiohttp.helpers.quote(term)}"
            "&tags=story"
            "&numericFilters=created_at_i>1700000000"  # 最近の記事
            f"&hitsPerPage={limit}"
        )
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
                for hit in data.get("hits", []):
                    tag = detect_tag(f"{hit.get('title', '')} {term}")
                    if not tag:
                        tag = detect_tag(term) or "chatgpt"
                    story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
                    results.append(Article(
                        title=hit.get("title", "")[:200],
                        url=story_url,
                        source="HackerNews",
                        score=hit.get("points", 0),
                        published=_parse_dt(hit.get("created_at", "")),
                        tag=tag,
                    ))
        except Exception:
            continue

    results.sort(key=lambda a: a.score, reverse=True)
    return results[:limit * len(TAGS)]


# ─── RSS フィード ──────────────────────────────────────────────────────────

async def fetch_rss(session: aiohttp.ClientSession, limit: int = 3) -> list[Article]:
    """公式ブログ RSS から最新記事を取得。"""
    results = []
    headers = {"User-Agent": "Mozilla/5.0 (AI-Discord-Bot)"}

    for source_name, feed_url in RSS_FEEDS:
        try:
            async with session.get(feed_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    continue
                text = await resp.text()
                root = ET.fromstring(text)
                ns = {"atom": "http://www.w3.org/2005/Atom"}

                # RSS 2.0
                items = root.findall(".//item")
                # Atom
                if not items:
                    items = root.findall(".//atom:entry", ns)

                for item in items[:10]:
                    title_el = item.find("title")
                    link_el  = item.find("link")
                    date_el  = (item.find("pubDate") or item.find("atom:published", ns)
                                or item.find("atom:updated", ns))

                    title = (title_el.text or "").strip() if title_el is not None else ""
                    link  = (link_el.text  or "").strip() if link_el  is not None else ""
                    # Atom の <link> は href 属性の場合がある
                    if not link and link_el is not None:
                        link = link_el.get("href", "")
                    date_str = (date_el.text or "") if date_el is not None else ""

                    if not title or not link:
                        continue

                    tag = detect_tag(f"{title} {source_name}")
                    if not tag:
                        # 公式ブログはソースからタグを推定
                        mapping = {
                            "OpenAI": "chatgpt", "Anthropic": "claude",
                            "Google AI": "gemini", "GitHub": "copilot", "Cursor": "cursor",
                        }
                        tag = mapping.get(source_name, "chatgpt")

                    results.append(Article(
                        title=title[:200],
                        url=link,
                        source=f"Blog({source_name})",
                        score=100,   # ブログ記事は常に高優先
                        published=_parse_dt(date_str) if date_str else datetime.now(tz=timezone.utc),
                        tag=tag,
                    ))
        except Exception:
            continue

    return results[:limit * len(TAGS)]


# ─── Twitter / X API v2 ────────────────────────────────────────────────────

async def fetch_twitter(session: aiohttp.ClientSession, limit: int = 5) -> list[Article]:
    """
    Twitter/X API v2 Recent Search でキーワード検索。
    TWITTER_BEARER_TOKEN 環境変数が必要。
    未設定の場合はスキップ。
    """
    bearer = os.getenv("TWITTER_BEARER_TOKEN", "")
    if not bearer:
        return []

    results = []
    queries = [
        "Gemini AI lang:ja OR lang:en -is:retweet",
        "ChatGPT lang:ja OR lang:en -is:retweet",
        "GitHub Copilot lang:ja OR lang:en -is:retweet",
        "Claude Anthropic lang:ja OR lang:en -is:retweet",
        "Cursor IDE lang:ja OR lang:en -is:retweet",
    ]
    headers = {"Authorization": f"Bearer {bearer}"}
    fields = "created_at,public_metrics,author_id"

    for query in queries:
        url = (
            "https://api.twitter.com/2/tweets/search/recent"
            f"?query={aiohttp.helpers.quote(query)}"
            f"&tweet.fields={fields}"
            f"&max_results={limit}"
            "&sort_order=relevancy"
        )
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
                for tweet in data.get("data", []):
                    tag = detect_tag(tweet.get("text", "") + " " + query)
                    if not tag:
                        continue
                    metrics = tweet.get("public_metrics", {})
                    results.append(Article(
                        title=tweet["text"][:140],
                        url=f"https://twitter.com/i/web/status/{tweet['id']}",
                        source="Twitter/X",
                        score=metrics.get("like_count", 0) + metrics.get("retweet_count", 0) * 3,
                        published=_parse_dt(tweet.get("created_at", "")),
                        tag=tag,
                    ))
        except Exception:
            continue

    results.sort(key=lambda a: a.score, reverse=True)
    return results[:limit * len(TAGS)]


# ─── メイン収集関数 ────────────────────────────────────────────────────────

async def fetch_all(limit_per_source: int = 5) -> dict[str, list[Article]]:
    """
    全ソースから収集してタグ別に分類して返す。
    戻り値: {"gemini": [...], "chatgpt": [...], ...}
    """
    async with aiohttp.ClientSession() as session:
        reddit, hn, rss, tw = await asyncio.gather(
            fetch_reddit(session, limit_per_source),
            fetch_hackernews(session, limit_per_source),
            fetch_rss(session, limit_per_source),
            fetch_twitter(session, limit_per_source),
        )

    all_articles = reddit + hn + rss + tw

    # タグ別に分類・重複URL除去・スコア降順
    categorized: dict[str, list[Article]] = {tag: [] for tag in TAGS}
    seen_urls = set()
    for article in sorted(all_articles, key=lambda a: a.score, reverse=True):
        if article.url in seen_urls:
            continue
        seen_urls.add(article.url)
        categorized[article.tag].append(article)

    # 各タグ上位 3 件に絞る
    return {tag: articles[:3] for tag, articles in categorized.items()}
