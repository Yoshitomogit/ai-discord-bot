# AI ニュース Discord ボット

Gemini / ChatGPT / Copilot / Claude / Cursor の最新情報を毎朝収集し、Discord に投稿するボット。

## ファイル構成

```
├── bot.py            # Discord ボット本体・スケジューラ・スラッシュコマンド
├── fetchers.py       # 情報収集モジュール（Reddit / HN / RSS / Twitter）
├── requirements.txt
├── .env.example      # 環境変数テンプレート
├── Procfile          # Heroku / Render 用
└── railway.toml      # Railway 用
```

## セットアップ

### 1. Discord Bot を作成

1. [Discord Developer Portal](https://discord.com/developers/applications) を開く
2. "New Application" → Bot タブ → "Reset Token" でトークン取得
3. OAuth2 → URL Generator で `bot` + `applications.commands` を選択
4. Bot Permissions: `Send Messages`, `Embed Links`, `Read Message History`
5. 生成された URL でサーバーに招待

### 2. 環境変数を設定

```bash
cp .env.example .env
# .env を編集して DISCORD_TOKEN と DISCORD_CHANNEL_ID を入力
```

### 3. ローカル動作確認

```bash
pip install -r requirements.txt
python bot.py
```

Discord サーバーで `/ainews` を実行して動作を確認してください。

---

## Railway へのデプロイ（無料枠）

1. [Railway](https://railway.app/) でアカウント作成
2. "New Project" → "Deploy from GitHub repo" → このリポジトリを選択
3. Variables タブで環境変数を設定（`.env` の内容をコピー）
4. 自動デプロイ完了 🎉

---

## スラッシュコマンド

| コマンド | 説明 |
|---------|------|
| `/ainews` | 今すぐ最新情報を取得して投稿 |
| `/ainews_help` | ボットの使い方を表示 |

---

## 自動投稿スケジュール

デフォルトは **毎日 00:00 UTC（日本時間 09:00）** に投稿。  
環境変数 `POST_HOUR` / `POST_MINUTE`（UTC）で変更可能。

---

## 情報ソース

| ソース | 内容 | 認証 |
|-------|------|------|
| Google News RSS (日本語) | 一般メディアの AI 関連報道 | 不要 |
| Reddit JSON API | r/ChatGPT 等のホットポスト | 不要 |
| Hacker News (Algolia) | キーワード検索 | 不要 |
| 公式ブログ RSS | OpenAI / Anthropic / Google / GitHub / Cursor | 不要 |
| Twitter/X API v2 | 関連ツイート | Bearer Token 必要 |

Twitter/X は `TWITTER_BEARER_TOKEN` 未設定でもボットは動作します（その収集のみスキップ）。

---

## 記事の選定ロジック

- **直近 5 日以内**に公開された記事のみを対象（`fetchers.py` の `RECENT_DAYS`）
- **関心度スコア**で並べ替え（`interest_score`）
  - 一般メディア報道（Google News）と公式ブログを加点
  - 規制・料金・教育・仕事など生活に関わるキーワードを加点
  - API・ベンチマーク等の技術用語が多い記事は減点
  - upvote 等のエンゲージメントは対数で頭打ち
- **投稿済み記事は再投稿しない**  
  投稿した記事の URL とタイトルを `posted_history.json` に保存し（60 日で失効）、
  GitHub Actions が同ファイルをコミットして永続化。タイトルがほぼ同じ記事
  （別メディアの同一話題）も除外される。

---

## Twitter/X API の取得方法（任意）

1. [developer.twitter.com](https://developer.twitter.com/) でアカウント作成
2. Free プランで Bearer Token を取得
3. `.env` に `TWITTER_BEARER_TOKEN=xxx` を追加
