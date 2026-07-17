"""
fetchers.py
各ソースから Gemini / ChatGPT / Copilot / Claude / Cursor の最新情報を取得するモジュール。

対応ソース:
  - Reddit JSON API  (認証不要)
  - Hacker News Algolia Search API (認証不要)
  - 公式ブログ RSS / Web スクレイピング
  - Google News RSS (日本語・一般メディア、認証不要)
  - Twitter/X API v2  (Bearer Token が必要)
"""

import asyncio
import math
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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

# Google News RSS (日本語) 検索クエリ — 一般メディアの報道を拾う
GOOGLE_NEWS_QUERIES = {
    "gemini":  "Google Gemini",
    "chatgpt": "ChatGPT OR OpenAI",
    "copilot": "Microsoft Copilot OR GitHub Copilot",
    "claude":  "Claude Anthropic",
    "cursor":  "Cursor AI エディタ",
}

# この日数より古い記事は投稿対象にしない（3日ごとの実行に余裕を持たせる）
RECENT_DAYS = 5

# 一般の関心を惹きやすい話題のキーワード（含まれるほど加点）
GENERAL_INTEREST_KEYWORDS = [
    # 生活・社会への影響
    "無料", "料金", "値上げ", "値下げ", "有料", "公開", "発表", "開始", "提供",
    "規制", "禁止", "訴訟", "著作権", "プライバシー", "偽情報", "詐欺",
    "教育", "学校", "受験", "仕事", "雇用", "転職", "副業", "日本", "国内",
    "音楽", "映画", "アニメ", "イラスト", "画像生成", "動画生成", "音声",
    "スマホ", "アプリ", "iphone", "android", "検索", "調査", "利用者",
    # 英語圏の一般ニュース
    "free", "price", "launch", "release", "ban", "lawsuit", "copyright",
    "privacy", "regulation", "school", "education", "job", "work",
    "music", "movie", "video", "image", "voice", "app", "smartphone",
]

# 技術者以外には伝わりにくいキーワード（含まれるほど減点）
TECHNICAL_KEYWORDS = [
    "api", "sdk", "cli", "benchmark", "fine-tun", "finetun", "quantiz",
    "inference", "token", "context window", "weights", "embedding",
    "paper", "arxiv", "rag", "mmlu", "gpu", "cuda", "self-host",
    "llama.cpp", "ollama", "vram", "open weights", "distill",
    "実装", "ベンチマーク", "推論速度", "量子化", "ローカルllm",
]


# ─── ユーティリティ ────────────────────────────────────────────────────────

def detect_tag(text: str) -> Optional[str]:
    """テキストに含まれるキーワードからタグを返す。複数マッチは最初のもの。"""
    lower = text.lower()
    for tag, keywords in TAGS.items():
        if any(kw in lower for kw in keywords):
            return tag
    return None


def title_key(title: str) -> str:
    """タイトルを正規化して重複判定用のキーを作る。

    Google News のタイトル末尾に付く「 - 媒体名」を除去し、
    記号・空白を落として同じ話題の記事を同一視できるようにする。
    """
    t = title
    if " - " in t:
        head, tail = t.rsplit(" - ", 1)
        if len(tail) <= 25:
            t = head
    t = re.sub(r"[^0-9a-zA-Zぁ-んァ-ヶ一-龠ー]", "", t.lower())
    return t[:80]


def interest_score(a: "Article") -> float:
    """一般の読者にとっての関心度を推定するスコア。

    エンゲージメント数そのままだと技術コミュニティの話題が常に上位に
    来るため、一般メディア報道・生活に関わるキーワードを加点し、
    技術用語の多い記事を減点する。
    """
    lower = f"{a.title} {a.summary}".lower()
    score = 0.0

    # 一般メディアに報道された話題を優先
    if a.source == "GoogleNews":
        score += 50
    elif a.source.startswith("Blog"):
        score += 25

    # エンゲージメントは対数で頭打ちにする（Reddit の数千 upvote に引きずられない）
    score += min(30.0, math.log10(max(a.score, 1)) * 10)

    boosts = sum(1 for kw in GENERAL_INTEREST_KEYWORDS if kw in lower)
    score += min(boosts, 3) * 15

    penalties = sum(1 for kw in TECHNICAL_KEYWORDS if kw in lower)
    score -= min(penalties, 3) * 20

    return score


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
    # 直近 RECENT_DAYS 日以内の記事のみ（固定値だと毎回同じ歴代人気記事が返る）
    cutoff_unix = int((datetime.now(tz=timezone.utc) - timedelta(days=RECENT_DAYS)).timestamp())

    for term in search_terms:
        url = (
            "https://hn.algolia.com/api/v1/search"
            f"?query={aiohttp.helpers.quote(term)}"
            "&tags=story"
            f"&numericFilters=created_at_i>{cutoff_unix}"
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


# ─── Google News RSS (日本語・一般メディア) ────────────────────────────────

async def fetch_google_news(session: aiohttp.ClientSession, limit: int = 5) -> list[Article]:
    """Google News RSS (日本語) から一般メディアの報道を取得。"""
    results = []
    headers = {"User-Agent": "Mozilla/5.0 (AI-Discord-Bot)"}

    for tag, query in GOOGLE_NEWS_QUERIES.items():
        encoded = urllib.parse.quote(f"{query} when:{RECENT_DAYS}d")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP:ja"
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    continue
                text = await resp.text()
                root = ET.fromstring(text)
                for item in root.findall(".//item")[:limit]:
                    title_el = item.find("title")
                    link_el = item.find("link")
                    date_el = item.find("pubDate")

                    title = (title_el.text or "").strip() if title_el is not None else ""
                    link = (link_el.text or "").strip() if link_el is not None else ""
                    date_str = (date_el.text or "") if date_el is not None else ""

                    if not title or not link:
                        continue

                    results.append(Article(
                        title=title[:200],
                        url=link,
                        source="GoogleNews",
                        score=0,  # エンゲージメント指標なし。interest_score のソース加点で優先される
                        published=_parse_dt(date_str) if date_str else datetime.now(tz=timezone.utc),
                        tag=tag,
                    ))
        except Exception:
            continue
        await asyncio.sleep(0.3)

    return results


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

async def fetch_all(
    limit_per_source: int = 5,
    exclude_urls: Optional[set[str]] = None,
    exclude_title_keys: Optional[set[str]] = None,
) -> dict[str, list[Article]]:
    """
    全ソースから収集してタグ別に分類して返す。
    戻り値: {"gemini": [...], "chatgpt": [...], ...}

    exclude_urls / exclude_title_keys に投稿済み記事の URL・タイトルキーを
    渡すと除外される（同じ記事の再投稿防止）。
    """
    exclude_urls = exclude_urls or set()
    exclude_title_keys = exclude_title_keys or set()

    async with aiohttp.ClientSession() as session:
        reddit, hn, rss, gnews, tw = await asyncio.gather(
            fetch_reddit(session, limit_per_source),
            fetch_hackernews(session, limit_per_source),
            fetch_rss(session, limit_per_source),
            fetch_google_news(session, limit_per_source),
            fetch_twitter(session, limit_per_source),
        )

    all_articles = reddit + hn + rss + gnews + tw
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=RECENT_DAYS)

    # タグ別に分類・古い記事と投稿済み/重複記事を除去・関心度スコア降順
    categorized: dict[str, list[Article]] = {tag: [] for tag in TAGS}
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for article in sorted(all_articles, key=interest_score, reverse=True):
        if article.published < cutoff:
            continue
        if article.url in exclude_urls or article.url in seen_urls:
            continue
        key = title_key(article.title)
        if key and (key in exclude_title_keys or key in seen_titles):
            continue
        seen_urls.add(article.url)
        if key:
            seen_titles.add(key)
        categorized[article.tag].append(article)

    # 各タグ上位 3 件に絞る
    return {tag: articles[:3] for tag, articles in categorized.items()}
