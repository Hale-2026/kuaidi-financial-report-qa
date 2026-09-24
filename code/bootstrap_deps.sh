#!/bin/zsh
# bootstrap_deps.sh —— 依赖自检 + 稳健补装（作业3-A）
#
# 背景：本机 Python 3.13 下 pip 解包 jieba / zhconv 这类纯 sdist 包会报
#       「EEXIST: file already exists」；且之前一轮 pip 安装整批没落地，
#       导致第 3 步 `import jieba` 直接崩掉。本脚本把「缺什么装什么 + 装完必验」
#       固化下来：pip 装不上就走「下源码包 → 手工解包 → 拷进 site-packages」。
#
# 用法：
#   cd "/Users/shaomengdan/WorkBuddy/快递公司财报/homework3"
#   zsh code/bootstrap_deps.sh
#
# 退出码：0 = 依赖齐全；1 = 仍有缺失（上方会列出缺哪些）

set -u

ROOT="/Users/shaomengdan/WorkBuddy/快递公司财报/homework3"
VENV="$HOME/.workbuddy/binaries/python/envs/default"
SP_PY="$VENV/bin/python"
PIP="$VENV/bin/pip"
MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"
JIEBA_URL="https://pypi.tuna.tsinghua.edu.cn/packages/c6/cb/18eeb235f833b726522d7ebed54f2278ce28ba9438e3135ab0278d9792a2/jieba-0.42.1.tar.gz"
export WB_ROOT="$ROOT"

if [ ! -x "$SP_PY" ]; then
  echo "!! 找不到虚拟环境：$SP_PY"
  echo "   先建：python3 -m venv \"$VENV\""
  exit 1
fi

export PIP_DISABLE_PIP_VERSION_CHECK=1
export TMPDIR="$(mktemp -d /tmp/pipbuild.XXXXXX)"

# ---- 取源码包：先 pip download，失败则从 simple 索引里抠链接 --------------
fetch_sdist() {
  local name="$1" fallback="${2:-}" tmp u
  tmp="$(mktemp -d "/tmp/${name}sdist.XXXXXX")"
  cd "$tmp" || return 1
  "$PIP" download --no-deps --no-binary :all: -d . -i "$MIRROR" "$name" \
    2>/dev/null | tail -2
  if ! ls ./*.tar.gz >/dev/null 2>&1; then
    echo "   pip download 没拿到，改从 simple 索引抠链接"
    u="$(curl -s --max-time 30 "$MIRROR/$name/" \
         | grep -o 'href="[^"]*\.tar\.gz[^"]*"' | tail -1 \
         | sed 's/href="//;s/"$//')"
    if [ -n "$u" ]; then
      case "$u" in
        http*) : ;;
        *) u="$MIRROR/$name/${u#./}" ;;
      esac
      curl -sL --max-time 180 -o "${name}.tar.gz" "$u"
    elif [ -n "$fallback" ]; then
      curl -sL --max-time 180 -o "${name}.tar.gz" "$fallback"
    fi
  fi
  ls ./*.tar.gz >/dev/null 2>&1 || { echo "   拿不到 ${name} 源码包"; return 1; }
  tar -xzf ./*.tar.gz 2>/dev/null || { echo "   解压失败"; return 1; }
  SD_DIR="$tmp"
  return 0
}

# ---- 手工安装：把解压出来的包目录直接拷进 site-packages -------------------
install_manually() {
  local name="$1" url="${2:-}" sp src
  fetch_sdist "$name" "$url" || return 1
  sp="$("$SP_PY" -c 'import site;print(site.getsitepackages()[0])')"
  src="$(find "$SD_DIR" -maxdepth 3 -type d -name "$name" | head -1)"
  if [ -z "$src" ]; then
    echo "   解压目录里没找到 $name 包目录"; return 1
  fi
  rm -rf "$sp/$name"
  cp -R "$src" "$sp/$name"
  [ -f "$src.py" ] && cp "$src.py" "$sp/"     # 兼容单文件模块形式
  return 0
}

echo "================ 1/4 常规依赖（有 wheel，直装）================"
"$PIP" install --no-cache-dir --retries 5 -i "$MIRROR" \
  pymupdf zhconv numpy onnxruntime tokenizers requests \
  beautifulsoup4 lxml tqdm flask rank_bm25 scikit-learn openpyxl 2>&1 | tail -8

echo ""
echo "================ 2/4 jieba（PyPI 只有 sdist，单独处理）================"
if "$SP_PY" -c "import jieba" >/dev/null 2>&1; then
  echo "   jieba 已就位，跳过"
else
  "$PIP" install --no-cache-dir --retries 3 -i "$MIRROR" jieba 2>&1 | tail -4
  if ! "$SP_PY" -c "import jieba" >/dev/null 2>&1; then
    echo "   pip 直装没成 → 手工解包拷贝"
    install_manually jieba "$JIEBA_URL" || echo "   !! jieba 兜底安装也失败"
  fi
fi

echo ""
echo "================ 3/4 zhconv（要有词典才算能用）================"
if "$SP_PY" -c "import zhconv;assert zhconv.convert('單票收入與毛利率','zh-cn')=='单票收入与毛利率'" >/dev/null 2>&1; then
  echo "   zhconv 正常（繁简转换可用）"
else
  echo "   zhconv 缺词典或未安装 → 手工解包拷贝"
  install_manually zhconv || echo "   !! zhconv 兜底安装也失败"
fi

echo ""
echo "================ 4/4 依赖自检 ================"
"$SP_PY" - <<'PYEOF'
import importlib, os, sys

mods = ["fitz", "jieba", "rank_bm25", "numpy", "onnxruntime", "tokenizers",
        "requests", "bs4", "lxml", "tqdm", "flask", "sklearn", "openpyxl"]
bad = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:                                        # noqa: BLE001
        bad.append(f"{m}({type(e).__name__})")

# zhconv 要做功能性检查：光 import 成功但缺词典一样用不了
try:
    import zhconv
    if zhconv.convert("單票收入與毛利率", "zh-cn") != "单票收入与毛利率":
        bad.append("zhconv(词典缺失)")
except Exception as e:                                            # noqa: BLE001
    bad.append(f"zhconv({type(e).__name__})")

# 向量模型文件
root = os.environ.get("WB_ROOT", ".")
md = os.path.join(root, "data", "model", "bge-small-zh-v1.5")
miss = [p for p in ("onnx/model.onnx", "tokenizer.json")
        if not os.path.exists(os.path.join(md, p))]
if miss:
    bad.append("向量模型:" + ",".join(miss))

if bad:
    print("❌ 仍缺：" + " ".join(bad))
    sys.exit(1)
print("✅ 依赖齐全")
PYEOF
RC=$?

echo ""
if [ "$RC" -ne 0 ]; then
  echo "!! 还有依赖没装上（见上面那行「仍缺：」）。"
  echo "   兜底：没装 jieba 也能跑 —— 建索引会自动降级为内置二元切分，只是分词精度略降。"
  exit 1
fi

echo "下一步（提取/切块已完成 34 份，会自动跳过，直接从建索引开始）："
echo "  cd \"$ROOT\""
echo "  zsh run_all.sh"
