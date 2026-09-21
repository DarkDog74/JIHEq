#!/bin/zsh

set -u
set -o pipefail

SCRIPT_DIR="${0:A:h}"
PYTHON_BIN="${SCRIPT_DIR}/.venv/bin/python"
DEPARTMENTS=("传输部" "核心网部" "互联网部" "网管支撑部" "扩充测评集")

if [[ ! -x "${PYTHON_BIN}" ]]; then
  print -u2 "找不到项目虚拟环境 Python：${PYTHON_BIN}"
  exit 2
fi

DEFAULT_EVAL_ROOT="$(PYTHONPATH="${SCRIPT_DIR}" "${PYTHON_BIN}" -c 'from core.path_config import project_path; print(project_path("evaluation_root", "评估集20"))')"
DEFAULT_OUTPUT_ROOT="$(PYTHONPATH="${SCRIPT_DIR}" "${PYTHON_BIN}" -c 'from core.path_config import project_path; print(project_path("default_output_root", "output"))')"
EVAL_ROOT="${1:-${DEFAULT_EVAL_ROOT}}"
RESULT_ROOT="${2:-${DEFAULT_OUTPUT_ROOT}/评估集20_全流程结果}"

if [[ ! -d "${EVAL_ROOT}" ]]; then
  print -u2 "评估集目录不存在：${EVAL_ROOT}"
  exit 2
fi

mkdir -p "${RESULT_ROOT}"
cd "${SCRIPT_DIR}" || exit 2

program_error=0

for department in "${DEPARTMENTS[@]}"; do
  source_dir="${EVAL_ROOT}/${department}"
  label_file="${EVAL_ROOT}/${department}label/ground_truth.v1.json"
  output_dir="${RESULT_ROOT}/${department}"

  print "========================================================================"
  print "开始处理：${department}"
  print "原始材料：${source_dir}"
  print "真实标签：${label_file}"
  print "输出目录：${output_dir}"
  print "========================================================================"

  if [[ ! -d "${source_dir}" ]]; then
    print -u2 "跳过：部门原始材料目录不存在：${source_dir}"
    program_error=1
    continue
  fi
  if [[ ! -f "${label_file}" ]]; then
    print -u2 "跳过：部门真实标签不存在：${label_file}"
    program_error=1
    continue
  fi

  "${PYTHON_BIN}" main.py batch-all-source \
    --source-dir "${source_dir}" \
    --output-dir "${output_dir}" \
    --unit-depth 1 \
    --overwrite
  run_status=$?

  # 退出码1表示存在业务稽核不通过，属于正常评估结果；退出码2才表示程序错误。
  if (( run_status == 2 )); then
    print -u2 "${department} 出现程序错误，请检查该部门输出。"
    program_error=1
  fi
done

"${PYTHON_BIN}" - "${RESULT_ROOT}" <<'PY'
import json
import sys
from pathlib import Path

result_root = Path(sys.argv[1])
departments = ("传输部", "核心网部", "互联网部", "网管支撑部", "扩充测评集")
aggregate = {
    "correct_items": 0,
    "scored_items": 0,
    "incorrect_items": 0,
    "truth_only_items": 0,
    "prediction_only_items": 0,
}
loaded = 0

print("\n================ 综合评估结果 ================")
for department in departments:
    summary_file = result_root / department / "评估结果" / "评分汇总.json"
    if not summary_file.is_file():
        print(f"[{department}] 缺少评分文件：{summary_file}")
        continue

    summary = json.loads(summary_file.read_text(encoding="utf-8-sig"))
    totals = summary["totals"]
    loaded += 1
    for key in aggregate:
        aggregate[key] += int(totals[key])

    print(
        f"[{department}] "
        f"{totals['score']} / {totals['max_score']} | "
        f"准确率={totals['accuracy']:.2%} | "
        f"不一致={totals['incorrect_items']} | "
        f"真值缺程序={totals['truth_only_items']} | "
        f"程序缺真值={totals['prediction_only_items']}"
    )

if loaded:
    scored = aggregate["scored_items"]
    accuracy = aggregate["correct_items"] / scored if scored else 0.0
    print("------------------------------------------------")
    print(f"已汇总部门：{loaded} / {len(departments)}")
    print(f"综合得分：{aggregate['correct_items']} / {scored}")
    print(f"综合准确率：{accuracy:.2%}")
    print(f"不一致项：{aggregate['incorrect_items']}")
    print(f"真实标签有、程序缺失：{aggregate['truth_only_items']}")
    print(f"程序有、真实标签缺失：{aggregate['prediction_only_items']}")
else:
    print("没有找到可汇总的部门评分文件。")
print("================================================")
PY

if (( program_error )); then
  exit 2
fi
exit 0
