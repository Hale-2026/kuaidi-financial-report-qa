#!/usr/bin/env zsh
# 把 homework3 仓库推送到 GitHub
#
# 用法:
#   zsh code/push_github.sh <github用户名> [仓库名]
#   例: zsh code/push_github.sh your-github-name kuaidi-financial-report-qa
#
# 前置条件（网页上各做一次即可）:
#   1) 把 ~/.ssh/id_ed25519.pub 的内容加到 GitHub -> Settings -> SSH and GPG keys
#   2) 在 GitHub 建一个空仓库（不要勾选 Add README / .gitignore / license）
#
# 幂等：可重复执行，remote 已存在会自动更新。

set -u

GH_USER="${1:-}"
REPO="${2:-kuaidi-financial-report-qa}"

if [[ -z "$GH_USER" ]]; then
  echo "用法: zsh code/push_github.sh <github用户名> [仓库名]" >&2
  echo "示例: zsh code/push_github.sh your-github-name kuaidi-financial-report-qa" >&2
  exit 2
fi

# --- 定位仓库根目录 ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT" || { echo "无法进入 $ROOT" >&2; exit 1; }

if [[ ! -d .git ]]; then
  echo "[x] $ROOT 不是 git 仓库" >&2
  exit 1
fi

echo "== 仓库根目录: $ROOT"

# --- 1. 工作区必须干净 ---
if [[ -n "$(git status --porcelain)" ]]; then
  echo "[!] 有未提交的改动，先提交再推："
  git status --short
  echo
  echo "    可以先跑： git add -A && git commit -m \"更新\""
  exit 1
fi
echo "== 工作区干净，当前提交: $(git rev-parse --short HEAD)"

# --- 2. 确保分支是 main ---
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$BRANCH" != "main" ]]; then
  echo "[!] 当前分支是 $BRANCH，将重命名为 main"
  git branch -M main
fi

# --- 3. SSH 授权自检 ---
echo "== 检查 GitHub SSH 授权 ..."
SSH_OUT="$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 -T git@github.com 2>&1 || true)"
if echo "$SSH_OUT" | grep -qi "successfully authenticated"; then
  echo "== SSH 已授权"
elif echo "$SSH_OUT" | grep -qi "permission denied"; then
  echo
  echo "[x] SSH 公钥未被 GitHub 认可 —— 这是当前唯一的阻塞点。"
  echo
  echo "    请复制下面这一整行，粘到 GitHub："
  echo "      https://github.com/settings/ssh/new"
  echo
  cat ~/.ssh/id_ed25519.pub 2>/dev/null || echo "    (找不到 ~/.ssh/id_ed25519.pub)"
  echo
  echo "    Title 随便填（如 MacBook-Air），Key type 选 Authentication Key，Add SSH key。"
  echo "    加好后重跑本脚本即可。"
  exit 1
else
  echo "[!] SSH 探测返回异常，继续尝试推送："
  echo "$SSH_OUT" | head -3
fi

# --- 4. 配置 remote ---
URL="git@github.com:$GH_USER/$REPO.git"
if git remote get-url origin >/dev/null 2>&1; then
  OLD="$(git remote get-url origin)"
  if [[ "$OLD" != "$URL" ]]; then
    echo "== 更新 remote origin: $OLD -> $URL"
    git remote set-url origin "$URL"
  else
    echo "== remote origin 已是 $URL"
  fi
else
  echo "== 添加 remote origin: $URL"
  git remote add origin "$URL"
fi

# --- 5. 推送 ---
echo "== 推送到 $URL ..."
PUSH_OUT="$(git push -u origin main 2>&1)" && PUSH_RC=0 || PUSH_RC=$?
echo "$PUSH_OUT"

if [[ $PUSH_RC -ne 0 ]]; then
  echo
  if echo "$PUSH_OUT" | grep -qi "repository not found"; then
    echo "[x] GitHub 上还没有这个仓库（或名字不对）。"
    echo "    去 https://github.com/new 建一个空仓库，名字填：$REPO"
    echo "    不要勾选 Add a README / .gitignore / license，然后重跑本脚本。"
  elif echo "$PUSH_OUT" | grep -qi "permission denied"; then
    echo "[x] SSH 公钥仍未授权，见上面的步骤。"
  else
    echo "[x] 推送失败，看上面的报错。"
  fi
  echo
  echo "备选方案（HTTPS + Personal Access Token，token 权限只需 repo）:"
  echo "  git remote set-url origin https://<用户名>:<TOKEN>@github.com/$GH_USER/$REPO.git"
  exit 1
fi

echo
echo "== 完成：https://github.com/$GH_USER/$REPO"
