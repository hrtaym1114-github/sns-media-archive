# SNS Media Archive

A local web app to bulk-download and incrementally back up videos and images
from social media accounts you follow — built on
[gallery-dl](https://github.com/mikf/gallery-dl).

> **これは何？**
> お気に入りのSNSアカウントの動画・画像を一括取得し、その後は差分だけ追記していけるローカルWebアプリ。
> Instagram / X (Twitter) / TikTok / Pinterest / Tumblr / Bluesky に対応。

<!-- スクリーンショット例: docs/screenshot.png を後で追加 -->

## ✨ 主な機能

- 🎬 動画・画像をまとめてダウンロード（カルーセル投稿含む）
- 🔄 「更新」ボタンで**差分のみ取得**（既取得分は自動スキップ）
- 🌐 **6つのSNS対応**：プロフィールURLを貼ると自動判別
- 🖼 ホバーで**拡大ポップ**するギャラリー、**← →キー**で前後切替
- 🗑 データ削除はアプリ内 `trash/` へ退避（復元可能）
- 🎯 動画は再生互換性の高い **H.264** で取得

## 🌍 対応SNS

| Instagram | X (Twitter) | TikTok | Pinterest | Tumblr | Bluesky |
|:--:|:--:|:--:|:--:|:--:|:--:|
| ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

プロフィールURLを貼り付けるとSNSを自動判別します（Instagram はユーザー名だけでも可）。

## 📦 必要なもの

- macOS（Linux/Windows は未検証）
- Python 3.10 以上
- [`gallery-dl`](https://github.com/mikf/gallery-dl) — `brew install gallery-dl`
- [`ffmpeg`](https://ffmpeg.org/) — `brew install ffmpeg`
- 各SNSにログイン済みの **Google Chrome**（cookieを使用）

## 🚀 起動

```bash
git clone <this-repo>
cd <repo-name>
bash start.sh        # サーバー起動 + ブラウザを開く
```

ブラウザで `http://127.0.0.1:8780/` が開きます。停止は:

```bash
pkill -f "python3 app.py"
```

## 🕹 使い方

| 操作 | 動作 |
|------|------|
| **アカウント登録** | 上部の入力欄にプロフィールURLを貼り「＋ アカウント登録」 |
| **更新（差分取得）** | カードのボタン。新着投稿だけを取得 |
| **ギャラリー** | 取得済みメディアを一覧。動画/画像で絞り込み・ホバーで拡大プレビュー |
| **個別表示** | サムネをクリックで再生／**← →キー**で前後の投稿に切替 |
| **解除** | カード右上の **✕** から「登録解除のみ」/「登録解除＋データ削除」を選択 |
| **停止** | 取得中はカードの「停止」ボタン。再開は再度「更新」 |

## 🗂 ディレクトリ構成

```
.
├── app.py              バックエンド（Python標準ライブラリのみ）
├── gdl_config.json     gallery-dl 設定
├── start.sh            起動スクリプト
├── web/                フロントエンド（HTML / CSS / JS）
├── data/accounts.json  登録アカウント（.gitignore）
├── media/<folder>/     取得メディア（.gitignore）
├── trash/              削除データ退避（.gitignore・手動で空にすると完全削除）
└── logs/               ジョブログ（.gitignore）
```

## 🛠 仕組み

- ダウンロードエンジン：[`gallery-dl`](https://github.com/mikf/gallery-dl)
- 認証：起動中の Chrome のログイン済みCookieを `--cookies-from-browser` で利用
- 差分管理：アカウントごとに `_archive.sqlite` を持ち、取得済みを記録
- バックエンドは Python 標準ライブラリの `ThreadingHTTPServer` のみ。追加依存なし

## ⚠️ 注意 / Disclaimer

- 短時間に大量取得すると各SNSからアクセス制限を受けることがあります。
  投稿ごとに1〜2.5秒の待機を入れています。
- 各SNSの利用規約・著作権法を遵守して、**個人的なバックアップ用途**でご利用ください。
- 取得した著作物の二次利用・再配布は権利者の許諾が必要です。
- This tool is provided as-is for personal archival use; you are responsible
  for complying with each platform's Terms of Service and applicable
  copyright law.

## License

[MIT](./LICENSE)
