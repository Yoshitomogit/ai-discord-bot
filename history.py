"""
history.py — 投稿済み記事の履歴を管理するモジュール。

同じ記事を繰り返し投稿しないよう、投稿した記事の URL とタイトルキーを
posted_history.json に保存する。GitHub Actions 実行時はワークフローが
このファイルをコミットして永続化する。
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fetchers import Article, title_key

HISTORY_FILE = Path(__file__).parent / "posted_history.json"
KEEP_DAYS = 60  # これより古い履歴は削除（ファイル肥大化防止）


def load_history() -> list[dict]:
    """履歴を読み込み、古いエントリを間引いて返す。"""
    try:
        entries = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        entries = []
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    return [e for e in entries if e.get("date", "") >= cutoff]


def exclude_sets(entries: list[dict]) -> tuple[set[str], set[str]]:
    """fetch_all に渡す (URL 集合, タイトルキー集合) を返す。"""
    urls = {e["url"] for e in entries if e.get("url")}
    keys = {e["title_key"] for e in entries if e.get("title_key")}
    return urls, keys


def record_posted(entries: list[dict], articles: list[Article]) -> None:
    """投稿した記事を履歴に追加する。"""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    for a in articles:
        entries.append({"url": a.url, "title_key": title_key(a.title), "date": today})


def save_history(entries: list[dict]) -> None:
    HISTORY_FILE.write_text(
        json.dumps(entries, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
