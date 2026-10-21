# immich-ops

ImmichのDocker Compose、HDD運用、S3バックアップの設定とスクリプト。
現在の配置先は `/home/ms/packages/nas/immich-app`。

## 管理対象

- `docker-compose*.yml`: ImmichとCloudflare Tunnel
- `.env.example`: 環境変数の雛形。実際の `.env` はGit管理しない
- `operations/immich.service`: HDDのマウントに連動するsystemdサービス
- `operations/immich-storage-check`: メディアディスクの検査
- `operations/immich-s3-inventory.py`: DBを読み取り、元ファイル一覧を生成
- `operations/immich-s3-backup.py`: 元ファイルをS3 Deep Archiveへ転送・確認

画像・動画、PostgreSQLのデータ、DBダンプ、秘密情報、実行ログ、転送対象一覧は管理対象外。

## サーバー運用

[HDD・systemdの運用手順](operations/README.md)を参照。
既存サーバーでは `.env` やDBを上書きせず、稼働中のサービスを停止せずに変更を確認する。
新規配置時は `.env.example` を `.env` にコピーし、パスと秘密情報を設定する。
HDDのUUIDとサービス定義内の絶対パスは、このサーバー向けの値になっている。

Cloudflare Tunnelのルーティング設定は [CLOUDFLARE_TUNNEL.md](CLOUDFLARE_TUNNEL.md)を参照。
起動・停止は `immich.service` を使用する。

## S3バックアップ

[S3バックアップ手順](operations/S3_BACKUP.md)を参照。
DBはファイル一覧の取得にのみ使用し、S3へのバックアップ対象は元画像・元動画に限定する。
自動の定期実行は未設定。

`prepare-photo-disk.sh` と `cutover-photo-disk.sh` は完了済みのHDD移行の記録。
稼働環境で再実行しない。

## 秘密情報のバックアップ

`.env` とAWSの設定・認証情報は、ユーザー指定のGoogle Sheetsへ保存する。
[専用スキル](.agents/skills/immich-secret-backup/SKILL.md)は `.agents/skills/` からセッションをまたいで利用できる。
このリポジトリを作業ディレクトリとして開き、`immich-secret-backup` を指定して保存・取得・復元を依頼する。
値はモデルへ表示せず、プログラムが転送・照合する。シート上のBase64は暗号化ではない。
Google Drive接続の認証は別途必要。スキルやリポジトリにトークンを保存しない。
