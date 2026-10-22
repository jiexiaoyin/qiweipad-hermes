#!/usr/bin/env bash
# 把开发树同步到生产插件目录（只同步，不重启网关）。
#
#   bash sync.sh            # → $HERMES_HOME/plugins/qwpad + 各 profile 的 plugins/qwpad
#   DRY_RUN=1 bash sync.sh  # 只看差异
#
# ⚠️ profile 级副本（profiles/<name>/plugins/qwpad）优先于 home 级，必须一起更新，
#    否则网关加载的仍是旧代码。
#
# 生产目录是只读产物：只在开发树改代码，再跑本脚本。
# 重启必须在外层 shell 手动执行（网关进程内重启会被安全策略拦），见 README。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_ROOT="${QWPAD_BACKUP_ROOT:-/data/backups/qwpad-plugin}"

echo "▶ 源: $SRC"

TARGETS=("$HERMES_HOME/plugins/qwpad")
while IFS= read -r dir; do
  TARGETS+=("$dir")
done < <(find "$HERMES_HOME/profiles" -maxdepth 3 -type d -path '*/plugins/qwpad' 2>/dev/null | sort)

sync_to() {
  local DST="$1"
  echo "▶ 目标: $DST"

  if [ -d "$DST" ]; then
    mkdir -p "$BACKUP_ROOT"
    local name
    name="$(printf '%s' "$DST" | sed "s#^$HERMES_HOME/##; s#/#-#g")"
    BACKUP="$BACKUP_ROOT/${name}-$STAMP"
    cp -a "$DST" "$BACKUP"
    echo "▶ 备份: $BACKUP"
  fi

  local RSYNC_ARGS=(-a --delete
    --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache'
    --exclude '.venv' --exclude 'tests' --exclude 'scripts' --exclude 'docs'
    --exclude 'pytest.ini' --exclude 'sync.sh' --exclude 'README.md'
    --exclude 'CHANGELOG.md')
  [ "${DRY_RUN:-0}" = "1" ] && RSYNC_ARGS+=(--dry-run -v)

  mkdir -p "$DST"
  rsync "${RSYNC_ARGS[@]}" "$SRC/qwpad/" "$DST/"
  rsync -a "$SRC/plugin.yaml" "$DST/plugin.yaml"

  echo "▶ 结构门禁"
  for f in __init__.py adapter.py client.py inbound.py outbound.py media.py state.py retained.py registration.py plugin.yaml; do
    [ -f "$DST/$f" ] || { echo "✗ 缺少 $f"; exit 1; }
  done

  echo "▶ md5 校验"
  local fail=0 rel a b
  while IFS= read -r rel; do
    a=$(md5sum "$SRC/qwpad/$rel" | awk '{print $1}')
    b=$(md5sum "$DST/$rel" | awk '{print $1}')
    if [ "$a" != "$b" ]; then
      echo "✗ $rel 不一致"; fail=1
    fi
  done < <(cd "$SRC/qwpad" && find . -name '*.py' -printf '%P\n')
  a=$(md5sum "$SRC/plugin.yaml" | awk '{print $1}')
  b=$(md5sum "$DST/plugin.yaml" | awk '{print $1}')
  [ "$a" = "$b" ] || { echo "✗ plugin.yaml 不一致"; fail=1; }
  [ $fail -eq 0 ] || exit 1
  echo "✓ $DST"
}

for target in "${TARGETS[@]}"; do
  sync_to "$target"
  echo
done

echo "✓ 同步完成（未重启网关）。生效需在外层 shell 重启网关，见 README。"
