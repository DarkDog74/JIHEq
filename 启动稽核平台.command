#!/bin/zsh

# 首次运行会在线安装所需环境；以后运行只做快速检查并启动系统。
set -euo pipefail

PROJECT_DIR="${0:A:h}"
PARSER_DIR="${AUDIT_PARSER_ROOT:-$PROJECT_DIR/parser}"
VENV_DIR="${AUDIT_VENV_DIR:-$PROJECT_DIR/.venv}"
APP_REQUIREMENTS="$PROJECT_DIR/requirements.txt"
PARSER_REQUIREMENTS="$PARSER_DIR/requirements.txt"
HOST="${AUDIT_HOST:-127.0.0.1}"
PORT="${AUDIT_PORT:-8765}"
export AUDIT_URL_PREFIX="${AUDIT_URL_PREFIX-/chuilei/jihe}"
# 本机 HTTP 启动允许普通 Cookie；服务器 HTTPS 部署应保持 Secure 默认值。
if [[ "$HOST" == "127.0.0.1" || "$HOST" == "localhost" ]]; then
  export AUDIT_COOKIE_SECURE="${AUDIT_COOKIE_SECURE:-false}"
fi
URL="http://$HOST:$PORT"
BREW_BIN=""
PYTHON_BIN=""
LIBREOFFICE_BIN=""

cd "$PROJECT_DIR"

log() {
  print -P "%F{cyan}[环境安装]%f $1"
}

fail() {
  print -P "%F{red}[无法启动]%f $1" >&2
  print ""
  read -r "reply?按回车键关闭窗口。"
  exit 1
}

find_brew() {
  if command -v brew >/dev/null 2>&1; then
    BREW_BIN="$(command -v brew)"
  elif [[ -x "/opt/homebrew/bin/brew" ]]; then
    BREW_BIN="/opt/homebrew/bin/brew"
  elif [[ -x "/usr/local/bin/brew" ]]; then
    BREW_BIN="/usr/local/bin/brew"
  fi
}

ensure_brew() {
  find_brew
  if [[ -n "$BREW_BIN" ]]; then
    return
  fi

  log "未检测到 Homebrew。Python 或 LibreOffice 缺失时需要用它完成在线安装。"
  print "即将从 Homebrew 官方仓库运行安装程序，过程中可能需要输入本机登录密码。"
  read -r "reply?是否继续安装 Homebrew？[y/N] "
  if [[ "$reply" != [yY] ]]; then
    fail "用户取消了 Homebrew 安装。"
  fi
  command -v curl >/dev/null 2>&1 || fail "系统缺少 curl，无法下载安装程序。"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  find_brew
  [[ -n "$BREW_BIN" ]] || fail "Homebrew 安装完成后仍未找到 brew 命令。"
}

python_is_supported() {
  "$1" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 15) else 1)' \
    >/dev/null 2>&1
}

find_python() {
  local candidate
  if [[ -n "${AUDIT_PYTHON:-}" ]] && [[ -x "$AUDIT_PYTHON" ]] && python_is_supported "$AUDIT_PYTHON"; then
    print "$AUDIT_PYTHON"
    return
  fi
  for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      candidate="$(command -v "$candidate")"
      if python_is_supported "$candidate"; then
        print "$candidate"
        return
      fi
    fi
  done
  return 1
}

ensure_python() {
  local candidate
  if candidate="$(find_python)"; then
    PYTHON_BIN="$candidate"
    return
  fi

  ensure_brew
  log "正在在线安装 Python 3.12……"
  "$BREW_BIN" install python@3.12
  PYTHON_BIN="$("$BREW_BIN" --prefix python@3.12)/bin/python3.12"
  [[ -x "$PYTHON_BIN" ]] || fail "Python 安装完成，但没有找到可执行文件。"
}

find_libreoffice() {
  local candidate
  if [[ -n "${LIBREOFFICE_PATH:-}" ]] && [[ -x "$LIBREOFFICE_PATH" ]]; then
    print "$LIBREOFFICE_PATH"
    return
  fi
  for candidate in \
    "$(command -v soffice 2>/dev/null || true)" \
    "$(command -v libreoffice 2>/dev/null || true)" \
    "$("$PYTHON_BIN" -c 'from core.path_config import soffice_candidates; print(soffice_candidates()[0] if soffice_candidates() else "")' 2>/dev/null || true)"; do
    if [[ -n "$candidate" ]] && [[ -x "$candidate" ]]; then
      print "$candidate"
      return
    fi
  done
  return 1
}

ensure_libreoffice() {
  local candidate
  if candidate="$(find_libreoffice)"; then
    LIBREOFFICE_BIN="$candidate"
    return
  fi

  ensure_brew
  log "正在在线安装 LibreOffice，用于解析 DOC、XLS 和 WPS 材料……"
  "$BREW_BIN" install --cask libreoffice
  LIBREOFFICE_BIN="$(find_libreoffice)" || fail "LibreOffice 安装完成，但没有找到 soffice。"
}

ensure_frontend() {
  if [[ -f "$PROJECT_DIR/frontend/dist/index.html" ]]; then
    return
  fi

  ensure_brew
  if ! command -v npm >/dev/null 2>&1; then
    log "网页构建文件不存在，正在在线安装 Node.js……"
    "$BREW_BIN" install node
    export PATH="$("$BREW_BIN" --prefix node)/bin:$PATH"
  fi
  log "正在构建网页界面……"
  (
    cd "$PROJECT_DIR/frontend"
    npm ci
    npm run build
  )
}

[[ "$(uname -s)" == "Darwin" ]] || fail "当前安装脚本仅支持 macOS。"
[[ -f "$PROJECT_DIR/app.py" ]] || fail "安装脚本必须放在程序根目录。"
[[ -f "$PARSER_DIR/parse_folder.py" ]] || fail "发布包缺少 parser/parse_folder.py。"
[[ -f "$APP_REQUIREMENTS" ]] || fail "发布包缺少 requirements.txt。"
[[ -f "$PARSER_REQUIREMENTS" ]] || fail "发布包缺少 parser/requirements.txt。"

ensure_python
ensure_libreoffice
ensure_frontend

if [[ -d "$VENV_DIR" ]] && [[ ! -x "$VENV_DIR/bin/python" ]]; then
  BACKUP_VENV="$PROJECT_DIR/.venv_旧环境_$(date +%Y%m%d_%H%M%S)"
  log "检测到不完整的旧环境，已保留为：${BACKUP_VENV:t}"
  mv "$VENV_DIR" "$BACKUP_VENV"
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  log "正在创建独立 Python 环境……"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
REQUIREMENTS_HASH="$("$VENV_PYTHON" -c '
import hashlib, pathlib, sys
digest = hashlib.sha256()
for name in sys.argv[1:]:
    digest.update(pathlib.Path(name).read_bytes())
print(digest.hexdigest())
' "$APP_REQUIREMENTS" "$PARSER_REQUIREMENTS")"
INSTALL_MARKER="$VENV_DIR/.requirements-$REQUIREMENTS_HASH"

if [[ ! -f "$INSTALL_MARKER" ]]; then
  log "正在在线安装文档解析、OCR 和网页服务依赖，首次运行需要几分钟……"
  "$VENV_PYTHON" -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
  "$VENV_PYTHON" -m pip install --disable-pip-version-check \
    -r "$APP_REQUIREMENTS" \
    -r "$PARSER_REQUIREMENTS"
  touch "$INSTALL_MARKER"
fi

export AUDIT_PARSER_ROOT="$PARSER_DIR"
export AUDIT_PARSER_PYTHON="$VENV_PYTHON"
export LIBREOFFICE_PATH="$LIBREOFFICE_BIN"
export AUDIT_HOST="$HOST"
export AUDIT_PORT="$PORT"
export PYTHONUNBUFFERED=1

UI_VERSION="$("$VENV_PYTHON" -c '
import hashlib, pathlib, sys
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()[:12])
' "$PROJECT_DIR/frontend/dist/index.html")"
BROWSER_URL="$URL/?ui=$UI_VERSION"

if curl -fsS "$URL/api/health" >/dev/null 2>&1; then
  log "系统已经运行，正在加载当前版本网页。"
  open "$BROWSER_URL"
  exit 0
fi

log "环境准备完成，正在启动浙江移动-定级备案报告稽核系统……"
(sleep 1.2; open "$BROWSER_URL") &
exec "$VENV_PYTHON" "$PROJECT_DIR/app.py"
