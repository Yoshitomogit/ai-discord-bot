"""
post_once.py — GitHub Actions 用ワンショット投稿スクリプト
Discord に接続 → ニュースを取得 → 日本語訳・概要付きで投稿 → 終了
"""

import asyncio
import os
from datetime import datetime, timezone

import discord
from deep_translator import GoogleTranslator
from dotenv import load_dotenv

from fetchers import fetch_all, Article
from history import load_history, exclude_sets, record_posted, save_history

load_dotenv()

TOKEN      = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])

TAG_META = {
    "gemini":  {"label": "Google Gemini",     "color": 0x4285F4, "emoji": "🔵"},
    "chatgpt": {"label": "ChatGPT / OpenAI",  "color": 0x10A37F, "emoji": "🟢"},
    "copilot": {"label": "GitHub Copilot",    "color": 0x6E40C9, "emoji": "🟣"},
    "claude":  {"label": "Claude / Anthropic","color": 0xD4A017, "emoji": "🟡"},
    "cursor":  {"label": "Cursor",            "color": 0xFF6B35, "emoji": "🟠"},
}

SOURCE_ICON = {"Reddit": "📋", "HackerNews": "🔶", "Twitter/X": "🐦", "GoogleNews": "🗞️"}

translator = GoogleTranslator(source="auto", target="ja")


def translate(text: str) -> str:
    """英語テキストを日本語に翻訳。失敗時は原文を返す。"""
    try:
        result = translator.translate(text[:4500])  # API 上限対策
        return result or text
    except Exception:
        return text


def make_embeds(categorized: dict) -> list[discord.Embed]:
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    embeds = []

    for tag, articles in categorized.items():
        meta = TAG_META[tag]
        embed = discord.Embed(
            title=f"{meta['emoji']} {meta['label']} — 最新トピック",
            color=meta["color"],
            timestamp=datetime.now(tz=timezone.utc),
        )
        embed.set_footer(text=f"AI News Bot • {now_str}")

        if not articles:
            embed.description = "_本日は新しいトピックが見つかりませんでした。_"
        else:
            for i, a in enumerate(articles, 1):
                icon = next((v for k, v in SOURCE_ICON.items() if k in a.source), "📰")
                score_label = f"⬆️ {a.score}" if a.score else ""

                # タイトル日本語訳
                ja_title = translate(a.title)

                # 概要（summary があれば翻訳、なければタイトル訳のみ）
                summary_line = ""
                if a.summary:
                    ja_summary = translate(a.summary)
                    summary_line = f"\n> {ja_summary[:120]}{'…' if len(ja_summary) > 120 else ''}"

                field_value = (
                    f"**🇯🇵 {ja_title}**{summary_line}\n"
                    f"[原文を読む → {a.title[:60]}{'…' if len(a.title) > 60 else ''}]({a.url})"
                )

                embed.add_field(
                    name=f"{i}. {icon} {a.source}  {score_label}",
                    value=field_value,
                    inline=False,
                )

        embeds.append(embed)
    return embeds


async def main():
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"ログイン: {client.user}")
        try:
            channel = client.get_channel(CHANNEL_ID)
            if channel is None:
                channel = await client.fetch_channel(CHANNEL_ID)

            print("情報収集中…")
            history = load_history()
            posted_urls, posted_titles = exclude_sets(history)
            categorized = await fetch_all(
                limit_per_source=5,
                exclude_urls=posted_urls,
                exclude_title_keys=posted_titles,
            )

            today = datetime.now(tz=timezone.utc).strftime("%Y/%m/%d")
            await channel.send(
                f"## 🤖 AI 最新情報ダイジェスト — {today}\n"
                "Gemini / ChatGPT / Copilot / Claude / Cursor の最新話題を日本語訳付きでお届けします。"
            )

            print("翻訳・Embed生成中…")
            embeds = make_embeds(categorized)
            for i in range(0, len(embeds), 10):
                await channel.send(embeds=embeds[i:i+10])
                await asyncio.sleep(1)

            # 投稿した記事を履歴に保存（次回以降の重複防止）
            record_posted(history, [a for arts in categorized.values() for a in arts])
            save_history(history)

            print("投稿完了。")
        finally:
            await client.close()

    await client.start(TOKEN)


asyncio.run(main())
