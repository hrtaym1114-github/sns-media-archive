#!/usr/bin/env bash
# Instagram メディアアーカイブ — 起動スクリプト
cd "$(dirname "$0")"

if curl -s -m 2 http://127.0.0.1:8780/api/state >/dev/null 2>&1; then
  echo "サーバーは既に起動しています。"
else
  mkdir -p logs
  nohup python3 app.py > logs/server.log 2>&1 &
  echo "サーバーを起動しました (PID $!)"
  sleep 2
fi

echo "ブラウザで開きます: http://127.0.0.1:8780/"
open "http://127.0.0.1:8780/"
