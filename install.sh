#!/usr/bin/env bash
# wxpost 安装脚本：建虚拟环境 + 装依赖 + 把 SKILL.md 部署到各客户端的 skills 目录
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "==> wxpost @ $HERE"

# 1) 虚拟环境
if [ ! -x "$HERE/.venv/bin/python" ] && [ ! -x "$HERE/.venv/Scripts/python.exe" ]; then
  echo "==> 创建虚拟环境"
  if command -v python3 >/dev/null 2>&1; then
    python3 -m venv "$HERE/.venv"
  else
    python -m venv "$HERE/.venv"
  fi
fi

PY="$HERE/.venv/bin/python"
if [ ! -x "$PY" ]; then PY="$HERE/.venv/Scripts/python.exe"; fi
if [ ! -x "$PY" ]; then
  echo "!! 虚拟环境创建失败，手动执行：python3 -m venv \"$HERE/.venv\"" >&2
  exit 1
fi

# 2) 依赖
echo "==> 安装依赖（requests / Pillow）"
"$PY" -m pip install -q --disable-pip-version-check --upgrade pip || true
"$PY" -m pip install -q --disable-pip-version-check requests Pillow

chmod +x "$HERE/wxpost.sh" "$HERE/wxpost.py" 2>/dev/null || true

# 3) 部署 SKILL.md
deploy() {
  mkdir -p "$1"
  cp "$HERE/SKILL.md" "$1/SKILL.md"
  echo "    SKILL.md -> $1"
}

echo "==> 部署 Skill"
deploy "$HOME/.workbuddy/skills/wxpost"
deploy "$HOME/.claude/skills/wxpost"
if [ -d "$HOME/.config/opencode" ]; then
  deploy "$HOME/.config/opencode/skills/wxpost"
fi
if [ -d "$HOME/.codex" ]; then
  echo "    Codex 没有 skills 目录，把 SKILL.md 内容追加到项目的 AGENTS.md 即可"
fi

# 4) 自检（没配凭证时返回非 0，不阻断安装）
echo "==> 自检"
"$PY" "$HERE/wxpost.py" check --no-api || true

cat <<EOF

安装完成。

下一步：
  $HERE/wxpost.sh init     # 配公众号凭证
  $HERE/wxpost.sh check    # 体检

Windows 用户把 wxpost.sh 换成 wxpost.cmd。
EOF
