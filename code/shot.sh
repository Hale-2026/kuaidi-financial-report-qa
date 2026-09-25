#!/bin/zsh
# shot.sh —— 给问答页面截图（零 npm 依赖，直接用本机 Chrome / Edge 无头模式）
#
# 用法：先起 Web 服务，再执行
#   "$V/python" code/app.py --port 8000 &
#   zsh code/shot.sh 8000
#
# 原理：页面支持 /?qi=N 参数（N 为预设场景序号），打开即自动提问，
#       无头浏览器等页面渲染完直接截图，不需要 Playwright 驱动交互。
set -u

PORT="${1:-8000}"
# 仓库根 = 本脚本上一级目录。不写死本机路径。
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SHOTS="$ROOT/shots"
mkdir -p "$SHOTS"

CH=""
for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
         "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" \
         "/Applications/Chromium.app/Contents/MacOS/Chromium" \
         "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"; do
  if [ -x "$c" ]; then CH="$c"; break; fi
done
if [ -z "$CH" ]; then
  echo "!! 没找到 Chrome / Edge / Chromium，无法截图"
  exit 1
fi
echo "浏览器: $CH"
echo "页面  : http://127.0.0.1:$PORT"

# 场景名与 web/index.html 里的 SCENES 顺序一一对应
NAMES=(
  "01_首页_混合检索_跨公司全景题"
  "02_答案与出处_单公司数值题"
  "03_跨市场对比_单票收入"
  "04_仅BM25召回对比"
  "05_仅向量召回对比"
  "06_表格类问题_经营活动现金流"
  "07_分组召回_跨公司全景题_改进后"
  "08_分组召回_跨市场口径对比_改进后"
)

snap() {   # snap <输出文件名> <URL>
  rm -f "$SHOTS/$1"
  # --no-sandbox：在受限环境（CI / 沙箱 / 远程会话）里 Chrome 自带的 sandbox
  #   起不来会直接 FATAL 退出；加这个参数后本地与受限环境都能截图。
  "$CH" --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage \
        --hide-scrollbars --no-first-run --no-default-browser-check \
        --disable-extensions \
        --force-device-scale-factor=1 --window-size=1400,1620 \
        --virtual-time-budget=40000 \
        --screenshot="$SHOTS/$1" "$2" >/dev/null 2>&1
  if [ -s "$SHOTS/$1" ]; then
    echo "OK   $1  ($(du -h "$SHOTS/$1" | cut -f1))"
  else
    echo "FAIL $1"
  fi
  sleep 1
}

BASE="http://127.0.0.1:$PORT"
snap "00_页面首页.png" "$BASE/?qi=-1"

i=0
for nm in "${NAMES[@]}"; do
  snap "${nm}.png" "$BASE/?qi=$i"
  i=$((i+1))
done

echo ""
echo "截图目录: $SHOTS"
ls -lh "$SHOTS"
