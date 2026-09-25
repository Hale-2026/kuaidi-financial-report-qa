#!/bin/zsh
# bootstrap_deps.sh —— 依赖自检 + 稳健补装（作业3-A）
#
# 背景（两个真实踩过的坑）：
#   1) `jieba` / `zhconv` 在 PyPI 上**只有 sdist 源码包**，本机 pip 解包它们会报
#        EEXIST: file already exists, mkdir '.../pip-install-xxxx/<pkg>_xxxx'
#      这是 pip 层的问题，换 TMPDIR、加 --no-cache-dir 都不管用。
#   2) 更坑的是：**这个报错会让整条 pip 命令 abort**。如果把 sdist 混在整批里
#      （原版就把 zhconv 混在 1/4 批里），同批已经下载好的 wheel 包也会一个都装不上
#      —— 干净环境实测：13 个包里最终只有 jieba / zhconv 靠手工兜底装上，其余全缺。
#   所以本脚本：整批只放有 wheel 的包 + 逐个复检补装；sdist 包单独走
#   「pip 先试 → 失败就下源码包手工解包拷进 site-packages」；最后统一功能自检。
#
# 用法：
#   cd <本仓库目录>
#   zsh code/bootstrap_deps.sh
#
# 退出码：0 = 依赖齐全；1 = 仍有缺失（上方会列出缺哪些）

set -u

# 仓库根 = 本脚本上一级目录。不写死本机路径。
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# venv 只认这两个：$VENV 环境变量 → 仓库内 .venv。
# 不写死任何本机路径，也不改用系统 Python（否则会把依赖装成全局包，污染本机）。
# 顺序与 run_all.sh 一致。
if [ -n "${VENV:-}" ]; then
  :
else
  VENV="$ROOT/.venv"
fi
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

# ---- 常规依赖：只放「有 wheel」的包 ---------------------------------------
# ⚠️ 千万别把只有 sdist 的包（jieba / zhconv）混进这一批：
#    本机 pip 解包纯 sdist 会报
#      EEXIST: file already exists, mkdir '.../pip-install-xxxx/<pkg>_xxxx'
#    而这个错误会让**整条 pip 命令 abort** —— 同批里已经下载好的 wheel 包
#    也一个都装不上（实测：混入 zhconv 后，pymupdf 等 12 个包全部没装成）。
#    这两个包各自单独处理（2/4、3/4），失败也只影响自己。
WHEEL_PKGS=(
  pymupdf numpy onnxruntime tokenizers requests beautifulsoup4
  lxml tqdm flask markdown rank_bm25 scikit-learn openpyxl
)
# 包名 → import 名（复检用）
mod_of() {
  case "$1" in
    beautifulsoup4) echo bs4 ;;
    scikit-learn)   echo sklearn ;;
    *)              echo "${1//-/_}" ;;
  esac
}

echo "================ 1/4 常规依赖（有 wheel，整批直装）================"
"$PIP" install --no-cache-dir --retries 5 -i "$MIRROR" "${WHEEL_PKGS[@]}" 2>&1 | tail -6

# 整批失败也不放弃：逐个复检，缺谁单独补装（一个包失败不连累其他包）
for p in "${WHEEL_PKGS[@]}"; do
  m="$(mod_of "$p")"
  if ! "$SP_PY" -c "import $m" >/dev/null 2>&1; then
    echo "   缺 $p → 单独补装"
    "$PIP" install --no-cache-dir --retries 5 -i "$MIRROR" "$p" 2>&1 | tail -3
    if "$SP_PY" -c "import $m" >/dev/null 2>&1; then
      echo "      ✅ $p 装上了"
    else
      echo "      ❌ $p 仍未装上（第 4/4 步自检会列出）"
    fi
  fi
done
echo "   常规依赖复检完成"

echo ""
echo "================ 2/4 jieba（PyPI 只有 sdist，单独处理）================"
if "$SP_PY" -c "import jieba" >/dev/null 2>&1; then
  echo "   jieba 已就位，跳过"
else
  LOG="$TMPDIR/jieba-pip.log"
  if "$PIP" install --no-cache-dir --retries 3 -i "$MIRROR" jieba >"$LOG" 2>&1 \
     && "$SP_PY" -c "import jieba" >/dev/null 2>&1; then
    echo "   pip 直装成功"
  else
    echo "   pip 直装没成（$(grep -m1 -o 'ERROR:.*' "$LOG" || echo "sdist 解包失败")）→ 手工解包拷贝"
    install_manually jieba "$JIEBA_URL" || echo "   !! jieba 兜底安装也失败"
  fi
fi

echo ""
echo "================ 3/4 zhconv（要有词典才算能用）================"
if "$SP_PY" -c "import zhconv;assert zhconv.convert('單票收入與毛利率','zh-cn')=='单票收入与毛利率'" >/dev/null 2>&1; then
  echo "   zhconv 正常（繁简转换可用）"
else
  LOG="$TMPDIR/zhconv-pip.log"
  if "$PIP" install --no-cache-dir --retries 3 -i "$MIRROR" zhconv >"$LOG" 2>&1 \
     && "$SP_PY" -c "import zhconv;assert zhconv.convert('單票收入與毛利率','zh-cn')=='单票收入与毛利率'" >/dev/null 2>&1; then
    echo "   pip 直装成功"
  else
    echo "   pip 直装没成（$(grep -m1 -o 'ERROR:.*' "$LOG" || echo "sdist 解包失败")）→ 手工解包拷贝"
    install_manually zhconv || echo "   !! zhconv 兜底安装也失败"
  fi
fi

echo ""
echo "================ 4/4 依赖自检 ================"
export DEP_REPORT="$TMPDIR/missing_deps.txt"
"$SP_PY" - <<'PYEOF'
import importlib, os, sys

mods = ["pymupdf", "jieba", "rank_bm25", "numpy", "onnxruntime", "tokenizers",
        "requests", "bs4", "lxml", "tqdm", "flask", "sklearn", "openpyxl",
        "markdown"]
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

# 把「pip 包缺失」单列一份，供上层区分「只差模型」与「还差包」两种情形
rp = os.environ.get("DEP_REPORT")
if rp:
    with open(rp, "w", encoding="utf-8") as f:
        f.write(" ".join(bad))

if bad or miss:
    print("❌ 仍缺：" + " ".join(bad + (["向量模型:" + ",".join(miss)] if miss else [])))
    sys.exit(1)
print("✅ 依赖齐全")
PYEOF
RC=$?

echo ""
if [ "$RC" -ne 0 ]; then
  # 区分两类缺口：pip 能装上的包 vs 必须单独下载的向量模型
  # （原来的提示一律说「还有依赖没装上」，只缺模型时答非所问，会误导）
  missing_pkgs="$(cat "$DEP_REPORT" 2>/dev/null || echo '')"
  model_miss=""
  if [ ! -f "$ROOT/data/model/bge-small-zh-v1.5/onnx/model.onnx" ] \
     || [ ! -f "$ROOT/data/model/bge-small-zh-v1.5/tokenizer.json" ]; then
    model_miss=1
    echo "!! 缺【向量模型】—— 它不是 pip 包，需要单独下载一次（96 MB，约 1 分钟）："
    echo ""
    echo "   cd \"$ROOT\""
    echo "   B=https://hf-mirror.com/Xenova/bge-small-zh-v1.5/resolve/main"
    echo "   D=data/model/bge-small-zh-v1.5 && mkdir -p \$D/onnx"
    echo "   for f in config.json tokenizer.json tokenizer_config.json special_tokens_map.json vocab.txt; do curl -sLo \$D/\$f \$B/\$f; done"
    echo "   curl -sLo \$D/onnx/model.onnx \$B/onnx/model.onnx"
    echo ""
    echo "   （国内直连 huggingface.co 通常不通，故走 hf-mirror 镜像；"
    echo "     下完目录应为 data/model/bge-small-zh-v1.5/{tokenizer.json, onnx/model.onnx}）"
    echo ""
  fi
  if [ -n "$missing_pkgs" ]; then
    echo "!! 还有 pip 包没装上：$missing_pkgs"
    echo "   （上面 1/4 与 2/4 的输出里有 pip 的原始报错，多为网络问题，重跑一次即可）"
  elif [ -n "$model_miss" ]; then
    echo "!! pip 包已全部就位，只差上面那个向量模型 —— 按那三条命令下完，再跑一次本脚本即为 0。"
  fi
  exit 1
fi

echo "下一步（提取/切块已完成 34 份，会自动跳过，直接从建索引开始）："
echo "  cd \"$ROOT\""
echo "  zsh run_all.sh"
