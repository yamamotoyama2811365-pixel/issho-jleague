# MATCHDAY | 一生Jリーグ

J1・J2・J3の全60クラブを起点に、全選手名鑑、スタジアム遠征ガイド、10問クイズ、ニックネーム付きランキングをまとめるファンポータルです。

## 今回実装済み

- 2026/27 J1・J2・J3 全60クラブのマスター
- 全選手をクラブ単位で同期する仕組み
- 選手検索・選手個別ページ
- ホームスタジアム名、収容人数、住所の同期
- スタジアム一覧・個別ガイドページ
- 公式座席図を転載しない独自の簡易観戦エリア図
- 10問固定のクイズフロー
- 正解数＋回答時間でサーバー側採点
- ニックネーム保存
- 総合 / 7日間 / 今日 のランキング
- SQLiteローカル動作 / Neon(PostgreSQL)本番動作
- Render用 `render.yaml`

## セットアップ

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py
```

ローカルでは SQLite を使います。本番では `DATABASE_URL` に Neon の PostgreSQL URL を設定します。

## 全選手・スタジアム基本情報の同期

```bash
RUN_FULL_SYNC=1 python scripts/sync_jleague.py
```

同期スクリプトは全60クラブの選手一覧ページを低頻度で順に読み、以下の事実項目だけを保存する設計です。

- 氏名
- ポジション
- 背番号
- 出生地 / 国籍表記
- 生年月日
- 身長 / 体重
- 当該シーズンの出場 / 得点
- ホームスタジアム名
- 入場可能数
- 住所
- 出典URL

写真、エンブレム、公式紹介文、公式座席図は保存・転載しません。各選手・スタジアムに公式ページへの出典リンクを残します。

## スタジアム情報の次段階

DBには次の追記欄を用意済みです。

- `seat_note`: 初観戦 / 応援重視 / 静かに観たい / 子連れ / 雨天など席選び
- `access_note`: 空港、主要駅、最寄駅、徒歩、駐車場
- `gourmet_note`: スタグル、売店
- `hotel_note`: 徒歩圏、駅前、遠征向けホテル
- `sightseeing_note`: 観光、お土産

公式座席図の画像自体は転載せず、公式情報を確認したうえで独自SVG図解へ落とし込む方針です。

## クイズランキングの不正対策

- 開始時にサーバー側で10問を抽選
- 正解はブラウザへ送らない
- 採点はサーバー側
- 回答時間もサーバー側の開始時刻〜送信時刻で算出
- 同じattempt_idは1回だけ保存

## 本番配信構成

- 通常ページ: https://issho-jleague.pages.dev/
- Cloudflare Pages: `issho-jleague`、GitHub `static-site` ブランチを配信。
- ビルドコマンド: なし。ルート・公開ディレクトリ: `/`。
- データ: Neon PostgreSQL。接続情報はGitHub Actions / RenderのSecretに保持。
- `Build static CDN frontend` がHTMLを生成して `static-site` を更新。CloudflareにはGitHub連携・本番ブランチ・自動デプロイ設定を登録済み。
- 初回公開と更新版の公開はCloudflare APIから成功。GitHub pushによる自動デプロイ発火は未確認のため、GitHub Appの対象リポジトリ権限を要確認。
- 主要10種類のページとCSS、日本語表示、選手検索は公開URLでブラウザ確認済み。
- 公開HTML全2,384ページの内部参照・文字化け・サイトマップ件数は静的検査済み。
- robots.txt / sitemap.xml のライブ取得は検証クライアントに403またはERR_BLOCKED_BY_CLIENTが返るため未確認。自動HTTP検証ジョブは誤警報を避けるため撤去。
- 選手検索・試合一覧の絞り込みは静的ページ内のJavaScriptで実行し、Renderへ問い合わせない。
- クイズは暫定的に https://issho-jleague.onrender.com/quiz へ移動し、Render / Neonを利用。
- Renderの通常デプロイでは全クラブ同期を実行しない。手動フル同期は `RUN_FULL_SYNC=1` の場合だけ。

2026-09-17: Cloudflare Pagesへ本番公開。60クラブ・2,256選手・59スタジアム、2,384ページ。
