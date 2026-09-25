#!/bin/zsh
# run_all.sh —— 一键跑完全流程（断点续跑，可反复执行）
#
# 用法（在本机「终端」里跑）：
#   cd <本仓库目录>          # 例如 clone 下来的 kuaidi-financial-report-qa/
#   zsh run_all.sh
#
# 每一步都幂等（与下文 0/7 – 7/7 的步骤号一一对应）：
#   1/7 下载      —— 已有 PDF 跳过（data/manifest.json 记录来源与校验）
#   2/7 提取切块  —— 已有分片跳过（按报告粒度续跑）
#   3/7 建索引    —— 已有向量分片跳过（按 500 块一片续跑）
#   4/7 评测      —— 覆盖输出（很快）
#   5/7 截图      —— 启服务 + 本机 Chrome 无头截图（零 npm 依赖）
#   6/7 出报告    —— Markdown → output/*.pdf（Chrome 无头打印）
#   7/7 打包      —— dist/作业3A_交付包_<日期>.zip（本地归档留档）
#
# 可选环境变量：
#   MAXW=3      提取并发数（默认 3；机器内存吃紧时降到 1–2）
#   SKIP_SHOT=1 跳过截图步骤
#   PORT=8000   Web 端口

set -u

# 仓库根 = 本脚本所在目录。不写死本机路径，别人 clone 下来一样能跑。
ROOT="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8000}"
MAXW="${MAXW:-3}"

cd "$ROOT" || { echo "!! 找不到目录 $ROOT"; exit 1; }
mkdir -p data/pdfs data/text data/chunks data/index output shots

# 找 Python：$VENV 环境变量 → 仓库内 .venv → 系统 python3
# 不写死任何本机 / 个人环境路径；系统 python3 只是最后兜底（一般没装这些依赖，
# 届时第 0 步会中止并提示先建 .venv，见 README 第 3 节）
CANDS=()
[ -n "${VENV:-}" ] && CANDS+=("$VENV/bin/python")
CANDS+=("$ROOT/.venv/bin/python")
_sys_py="$(command -v python3 2>/dev/null || true)"
[ -n "$_sys_py" ] && CANDS+=("$_sys_py")

PY=""
for cand in "${CANDS[@]}"; do
  if [ -x "$cand" ]; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "!! 找不到可用的 Python"
  echo "   先建虚拟环境：python3 -m venv \"$ROOT/.venv\""
  echo "   再装依赖    ：\"$ROOT/.venv/bin/pip\" install -r \"$ROOT/code/requirements.txt\""
  exit 1
fi
echo "Python: $PY"

step() { echo ""; echo "================ $* ================"; }
tick() { echo "   … $(date '+%H:%M:%S')"; }

step "0/7 环境自检（缺依赖会自动补装，仍缺则中止）"
check_deps() {
  WB_ROOT="$ROOT" "$PY" - <<'PYEOF'
import importlib, os, sys

def has(m):
    try:
        importlib.import_module(m)
        return True
    except Exception:                                             # noqa: BLE001
        return False

req = ["pymupdf", "numpy", "onnxruntime", "tokenizers",
       "requests", "bs4", "lxml", "tqdm", "flask", "zhconv", "markdown"]
opt = ["jieba", "rank_bm25", "sklearn", "openpyxl"]

bad = [m for m in req if not has(m)]
soft = [m for m in opt if not has(m)]

if soft:
    note = {"jieba": "分词降级为内置二元切分",
            "rank_bm25": "BM25 改用自实现版本",
            "sklearn": "无 TF-IDF 兜底向量",
            "openpyxl": "无 Excel 导出"}.get
    print("可选依赖缺失（有降级方案，可继续）: "
          + " ".join(f"{m}[{note(m, '')}]" if note(m) else m for m in soft))

root = os.environ.get("WB_ROOT", ".")
md = os.path.join(root, "data", "model", "bge-small-zh-v1.5")
miss = [p for p in ("onnx/model.onnx", "tokenizer.json")
        if not os.path.exists(os.path.join(md, p))]
if miss:
    print("缺向量模型: " + " ".join(miss))
    print("  → 向量模型不是 pip 包，需单独下载一次（96 MB，约 1 分钟）：")
    print(f'      cd "{root}"')
    print("      B=https://hf-mirror.com/Xenova/bge-small-zh-v1.5/resolve/main")
    print("      D=data/model/bge-small-zh-v1.5 && mkdir -p $D/onnx")
    print("      for f in config.json tokenizer.json tokenizer_config.json "
          "special_tokens_map.json vocab.txt; do curl -sLo $D/$f $B/$f; done")
    print("      curl -sLo $D/onnx/model.onnx $B/onnx/model.onnx")
    print("  → tokenizer 放在模型根目录、model.onnx 放在 onnx/ 子目录，别放反")

if bad or miss:
    print("缺必需依赖: " + " ".join(bad) + ("  [向量模型]" if miss else ""))
    sys.exit(1)
print("必需依赖 OK")
PYEOF
}

if check_deps; then
  echo "   环境没问题"
else
  # 向量模型不是 pip 包，bootstrap 装不了 —— 直接给下载指引，别白跑一遍装包
  if [ ! -f "$ROOT/data/model/bge-small-zh-v1.5/onnx/model.onnx" ] \
     || [ ! -f "$ROOT/data/model/bge-small-zh-v1.5/tokenizer.json" ]; then
    echo ""
    echo "!! 先按上面的命令下载向量模型，再重新跑 zsh run_all.sh"
    echo "   （bootstrap_deps.sh 只装 pip 包，解决不了模型缺失）"
    exit 1
  fi
  echo "→ 自动补装依赖：code/bootstrap_deps.sh"
  zsh "$ROOT/code/bootstrap_deps.sh" || true
  if ! check_deps; then
    echo ""
    echo "!! 依赖仍不齐，已中止 —— 先修好依赖再跑，别白跑一遍提取"
    exit 1
  fi
fi
tick

step "1/7 下载财报（缺失的才下）"
"$PY" code/01_download.py || echo "!! 下载步骤有失败项（见上），继续后续步骤"
tick

step "2/7 提取文字 + 表格还原 + 切块（并发 $MAXW）"
MAXW="$MAXW" "$PY" code/02_extract_chunk.py || { echo "!! 提取失败，中止"; exit 1; }
tick

step "3/7 建向量索引 + BM25 索引（CPU 编码，耗时较长）"
"$PY" code/03_build_index.py || { echo "!! 建索引失败，中止"; exit 1; }
tick

step "4/7 10 道题 × 4 种检索模式评测（含分组召回对照）"
"$PY" code/05_eval.py || echo "!! 评测有失败项（见上）"
tick

if [ "${SKIP_SHOT:-0}" = "1" ]; then
  echo ""
  echo "（已按 SKIP_SHOT=1 跳过截图）"
else
  step "5/7 起问答页面 + 截图"
  "$PY" code/app.py --port "$PORT" >output/_server.log 2>&1 &
  APP_PID=$!
  # 等端口就绪（最多 60s）
  for i in $(seq 1 60); do
    if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/meta"; then break; fi
    sleep 1
  done
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/meta"; then
    zsh code/shot.sh "$PORT"
  else
    echo "!! Web 服务没起来，看 output/_server.log"
    tail -20 output/_server.log
    echo "   可手工跑：\"$PY\" code/app.py --port $PORT  然后浏览器打开 http://127.0.0.1:$PORT"
  fi
  kill $APP_PID 2>/dev/null
fi

step "6/7 生成 PDF 报告（一页结论 + 评测台账）"
[ -f output/一页结论.md ] || echo "   提示：output/一页结论.md 不存在，报告将只含评测台账"
"$PY" code/make_report.py || echo "!! 报告生成失败 —— 需已装 markdown 库 + 本机 Google Chrome（具体报错见上）"
tick

step "7/7 打交付包"
"$PY" code/package.py || echo "!! 打包失败"
tick

echo ""
echo "================== 全部完成 =================="
echo "★ 要提交的包  : $ROOT/dist/作业3A_交付包_<日期>.zip"
echo "正式报告 PDF  : $ROOT/output/作业3A_报告.pdf"
echo "一页结论      : $ROOT/output/一页结论.pdf"
echo "问答页面截图 : $ROOT/shots/"
echo "评测台账     : $ROOT/output/eval_table.md"
echo "逐题明细     : $ROOT/output/eval_runs.json"
echo "知识库切块   : $ROOT/data/chunks/chunks.jsonl"
echo "检索索引     : $ROOT/data/index/"
echo ""
echo "手工看页面："
echo "  \"$PY\" code/app.py --port $PORT"
echo "  浏览器打开 http://127.0.0.1:$PORT"
