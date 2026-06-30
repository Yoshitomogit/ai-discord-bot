"""
post_shindanshi.py — GitHub Actions 用ワンショット投稿スクリプト
Discord に接続 → 中小企業診断士関連ニュースを取得 → 投稿 → 終了
"""

import asyncio
import os
from datetime import datetime, timezone

import discord
from dotenv import load_dotenv

from fetchers_shindanshi import fetch_all, CATEGORIES

load_dotenv()

TOKEN      = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ["SHINDANSHI_CHANNEL_ID"])


def make_embeds(categorized: dict) -> list[discord.Embed]:
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    embeds = []

    for cat_key, articles in categorized.items():
        meta = CATEGORIES[cat_key]
        embed = discord.Embed(
            title=f"{meta['emoji']} {meta['label']}",
            color=meta["color"],
            timestamp=datetime.now(tz=timezone.utc),
        )
        embed.set_footer(text=f"中小企業診断士 News Bot • {now_str}")

        if not articles:
            embed.description = "_本日は新しいトピックが見つかりませんでした。_"
        else:
            for i, a in enumerate(articles, 1):
                source_icon = {"Google News": "📰", "J-SMECA": "🏛️", "中小企業庁": "🏢"}.get(a.source, "📰")
                summary_line = f"\n> {a.summary}" if a.summary else ""

                field_value = (
                    f"**{a.title}**{summary_line}\n"
                    f"[{source_icon} {a.source} で読む]({a.url})"
                )
                embed.add_field(
                    name=f"{i}.",
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
            categorized = await fetch_all()

            today = datetime.now(tz=timezone.utc).strftime("%Y/%m/%d")
            await channel.send(
                f"## 📋 中小企業診断士ニュースダイジェスト — {today}\n"
                "施策・補助金・融資・事業承継・労務・試験情報をお届けします。"
            )

            print("Embed生成中…")
            embeds = make_embeds(categorized)
            for i in range(0, len(embeds), 10):
                await channel.send(embeds=embeds[i:i+10])
                await asyncio.sleep(1)

            print("投稿完了。")
        finally:
            await client.close()

    await client.start(TOKEN)


asyncio.run(main())
