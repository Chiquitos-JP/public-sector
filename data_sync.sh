#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# R2（public-sector-data）→ ローカル data/ の取り込み。
#
#   ./data_sync.sh pull     # R2 → ローカル（bronze / silver / gold / docs）
#   ./data_sync.sh status   # R2 とローカルの差分（ドライラン）
#
# R2 に書くのは定期取得の CI（.github/workflows/fetch.yml）だけ。手元からは push しない
# （検証を通っていない版や古いコードで作った版が混ざるのを防ぐ）。
# 前提: rclone remote `r2ps`（public-sector-data 限定のトークン）を登録済み。
# raw（API の応答そのまま）は監査用に R2 に残すだけで、手元には持ってこない。
# -----------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")"

REMOTE="r2ps:public-sector-data"
LAYERS=(bronze silver gold docs)

command -v rclone >/dev/null 2>&1 || { echo "❌ rclone が見つからない"; exit 1; }

case "${1:-pull}" in
  pull)
    for layer in "${LAYERS[@]}"; do
      echo "▼ R2 -> local ($layer)"
      # --update: ローカルの方が新しいファイルは上書きしない
      rclone copy "$REMOTE/$layer" "data/$layer" --update --transfers 8
    done
    echo "✅ pull 完了"
    ;;
  status)
    for layer in "${LAYERS[@]}"; do
      echo "=== $layer ==="
      rclone check "$REMOTE/$layer" "data/$layer" --one-way 2>&1 | tail -5
    done
    ;;
  *)
    echo "usage: $0 pull|status"; exit 1
    ;;
esac
