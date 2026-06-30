"""
post_once.py — GitHub Actions 用ワンショット投稿スクリプト
Discord に接続 → ニュースを取得 → 投稿 → 終了
"""

import asyncio
import os
from datetime import datetime, timezone

import discord
from dotenv import load_dotenv

from fetchers import fetch_all

load_dotenv()

TOKEN      = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])

TAG_META = {
    "gemini":  {"label": "Google Gemini",    "color": 0x4285F4, "emoji": "🔵"},
    "chatgpt": {"label": "ChatGPT / OpenAI", "color": 0x10A37F, "emoji": "🟢"},
    "copilot": {"label": "GitHub Copilot",   "color": 0x6E40C9, "emoji": "🟣"},
    "claude":  {"label": "Claude / Anthropic","color": 0xD4A017, "emoji": "🟡"},
    "cursor":  {"label": "Cursor",           "color": 0xFF6B35, "emoji": "🟠"},
}

SOURCE_ICON = {"Reddit": "📋", "HackerNews": "🔶", "Twitter/X": "🐦"}


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
                embed.add_field(
                    name=f"{i}. {icon} {a.source}  {score_label}",
                    value=f"[{a.title}]({a.url})",
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

            categorized = await fetch_all(limit_per_source=5)

            today = datetime.now(tz=timezone.utc).strftime("%Y/%m/%d")
            await channel.send(
                f"## 🤖 AI 最新情報ダイジェスト — {today}\n"
                "Gemini / ChatGPT / Copilot / Claude / Cursor の話題をお届けします。"
            )

            embeds = make_embeds(categorized)
            for i in range(0, len(embeds), 10):
                await channel.send(embeds=embeds[i:i+10])
                await asyncio.sleep(1)

            print("投稿完了。")
        finally:
            await client.close()

    await client.start(TOKEN)


asyncio.run(main())
