#!/bin/zsh
# run_all.sh —— 一键跑完全流程（断点续跑，可反复执行；不依赖 WorkBuddy 沙箱）
#
# 用法（在本机「终端」里跑，绕开 WorkBuddy 的执行沙箱）：
#   cd "/Users/shaomengdan/WorkBuddy/快递公司财报/homework3"
#   zsh run_all.sh
#
# 每一步都幂等：
#   ① 下载      —— 已有 PDF 跳过（data/manifest.json 记录来源与校验）
#   ② 提取切块  —— 已有分片跳过（按报告粒度续跑）
#   ③ 建索引    —— 已有向量分片跳过（按 500 块一片续跑）
#   ⑤ 评测      —— 覆盖输出（很快）
#   ⑥ 截图      —— 启服务 + 本机 Chrome 无头截图（零 npm 依赖）
#   ⑦ 出报告    —— Markdown → output/*.pdf（Chrome 无头打印）
#   ⑧ 打包      —— dist/作业3A_交付包_<日期>.zip（提交这个）
#
# 可选环境变量：
#   MAXW=3      提取并发数（默认 3；机器内存吃紧时降到 1–2）
#   SKIP_SHOT=1 跳过截图步骤
#   PORT=8000   Web 端口

set -u
ROOT="/Users/shaomengdan/WorkBuddy/快递公司财报/homework3"
V="$HOME/.workbuddy/binaries/python/envs/default/bin"
PORT="${PORT:-8000}"
MAXW="${MAXW:-3}"

cd "$ROOT" || { echo "!! 找不到目录 $ROOT"; exit 1; }
mkdir -p data/pdfs data/text data/chunks data/index output shots

PY="$V/python"
if [ ! -x "$PY" ]; then
  echo "!! 找不到虚拟环境：$PY"
  echo "   先建环境：python3 -m venv \"$HOME/.workbuddy/binaries/python/envs/default\""
  exit 1
fi

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

req = ["fitz", "numpy", "onnxruntime", "tokenizers",
       "requests", "bs4", "lxml", "tqdm", "flask", "zhconv"]
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

if bad or miss:
    print("缺必需依赖: " + " ".join(bad) + ("  [向量模型]" if miss else ""))
    sys.exit(1)
print("必需依赖 OK")
PYEOF
}

if check_deps; then
  echo "   环境没问题"
else
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
"$PY" code/make_report.py || echo "!! 报告生成失败（需本机 Google Chrome）"
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
echo "  \"$V/python\" code/app.py --port $PORT"
echo "  浏览器打开 http://127.0.0.1:$PORT"
