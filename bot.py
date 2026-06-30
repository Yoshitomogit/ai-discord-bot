"""
bot.py — AI ニュース Discord ボット
=====================================
Gemini / ChatGPT / Copilot / Claude / Cursor の最新情報を毎朝収集し、
指定 Discord チャンネルに投稿する。

スラッシュコマンド:
  /ainews          — 今すぐ最新情報を投稿
  /ainews_help     — ボットの説明を表示

必須環境変数 (.env に記述):
  DISCORD_TOKEN        — Discord Bot Token
  DISCORD_CHANNEL_ID   — 投稿先チャンネル ID (数字)

任意環境変数:
  TWITTER_BEARER_TOKEN — Twitter/X API Bearer Token
  POST_HOUR            — 投稿時刻 (UTC 時 / デフォルト: 0 = 日本時間 9:00)
  POST_MINUTE          — 投稿時刻 (UTC 分 / デフォルト: 0)
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from discord import app_commands
from dotenv import load_dotenv

from fetchers import fetch_all

# ─── 設定 ──────────────────────────────────────────────────────────────────

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("ai-bot")

TOKEN      = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])
POST_HOUR  = int(os.getenv("POST_HOUR",   "0"))   # UTC 0:00 = JST 9:00
POST_MIN   = int(os.getenv("POST_MINUTE", "0"))


# ─── Discord Embed 生成 ────────────────────────────────────────────────────

TAG_META = {
    "gemini":  {"label": "Google Gemini",   "color": 0x4285F4, "emoji": "🔵"},
    "chatgpt": {"label": "ChatGPT / OpenAI","color": 0x10A37F, "emoji": "🟢"},
    "copilot": {"label": "GitHub Copilot",  "color": 0x6E40C9, "emoji": "🟣"},
    "claude":  {"label": "Claude / Anthropic","color": 0xD4A017,"emoji": "🟡"},
    "cursor":  {"label": "Cursor",          "color": 0xFF6B35, "emoji": "🟠"},
}

SOURCE_ICON = {
    "Reddit":    "📋",
    "HackerNews":"🔶",
    "Twitter/X": "🐦",
}


def make_embeds(categorized: dict) -> list[discord.Embed]:
    """タグごとに Discord Embed を生成して返す。"""
    embeds = []
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

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
            embeds.append(embed)
            continue

        for i, a in enumerate(articles, 1):
            # ソースアイコン
            icon = next(
                (v for k, v in SOURCE_ICON.items() if k in a.source),
                "📰"
            )
            score_label = f"⬆️ {a.score}" if a.score else ""
            field_name  = f"{i}. {icon} {a.source}  {score_label}"
            field_value = f"[{a.title}]({a.url})"
            embed.add_field(name=field_name, value=field_value, inline=False)

        embeds.append(embed)

    return embeds


# ─── 投稿処理 ──────────────────────────────────────────────────────────────

async def post_news(channel: discord.TextChannel) -> None:
    """情報を収集して Discord に投稿する。"""
    log.info("情報収集を開始します…")
    try:
        categorized = await fetch_all(limit_per_source=5)
    except Exception as e:
        log.error(f"情報収集エラー: {e}")
        await channel.send("⚠️ 情報収集中にエラーが発生しました。しばらくしてから再試行してください。")
        return

    # ヘッダーメッセージ
    today = datetime.now(tz=timezone.utc).strftime("%Y/%m/%d")
    header = (
        f"## 🤖 AI 最新情報ダイジェスト — {today}\n"
        f"Gemini / ChatGPT / Copilot / Claude / Cursor の話題をお届けします。"
    )
    await channel.send(header)

    # Embed を 10 個ずつ送信 (Discord の上限)
    embeds = make_embeds(categorized)
    for i in range(0, len(embeds), 10):
        await channel.send(embeds=embeds[i:i+10])
        await asyncio.sleep(1)

    log.info("投稿完了。")


# ─── Bot 本体 ──────────────────────────────────────────────────────────────

class AINewsBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree      = app_commands.CommandTree(self)
        self.scheduler = AsyncIOScheduler()

    async def setup_hook(self):
        """起動時: スラッシュコマンドの同期とスケジューラ起動。"""
        await self.tree.sync()
        log.info("スラッシュコマンドを同期しました。")

        self.scheduler.add_job(
            self._scheduled_post,
            CronTrigger(hour=POST_HOUR, minute=POST_MIN, timezone="UTC"),
            id="daily_news",
        )
        self.scheduler.start()
        log.info(f"スケジューラ起動: 毎日 {POST_HOUR:02d}:{POST_MIN:02d} UTC に投稿します。")

    async def _scheduled_post(self):
        channel = self.get_channel(CHANNEL_ID)
        if channel is None:
            log.warning(f"チャンネル {CHANNEL_ID} が見つかりません。")
            return
        await post_news(channel)

    async def on_ready(self):
        log.info(f"ログイン完了: {self.user} (id={self.user.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="AI ニュース 📡"
            )
        )


# ─── スラッシュコマンド ────────────────────────────────────────────────────

bot = AINewsBot()


@bot.tree.command(name="ainews", description="AI 最新情報を今すぐ取得して投稿します")
async def ainews(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    channel = interaction.channel
    if channel is None:
        await interaction.followup.send("❌ チャンネルが取得できませんでした。")
        return
    await post_news(channel)
    await interaction.followup.send("✅ 最新情報を投稿しました！", ephemeral=True)


@bot.tree.command(name="ainews_help", description="AI ニュースボットの使い方を表示します")
async def ainews_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 AI ニュースボット — ヘルプ",
        description=(
            "Gemini / ChatGPT / Copilot / Claude / Cursor の最新話題を自動収集して投稿します。"
        ),
        color=0x5865F2,
    )
    embed.add_field(
        name="/ainews",
        value="今すぐ最新情報を取得して投稿します。",
        inline=False,
    )
    embed.add_field(
        name="自動投稿",
        value=f"毎日 {POST_HOUR:02d}:{POST_MIN:02d} UTC（日本時間 {(POST_HOUR+9)%24:02d}:{POST_MIN:02d}）に自動投稿されます。",
        inline=False,
    )
    embed.add_field(
        name="情報ソース",
        value="📋 Reddit  •  🔶 Hacker News  •  📰 公式ブログ RSS  •  🐦 Twitter/X (任意)",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─── 起動 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(TOKEN, log_handler=None)
