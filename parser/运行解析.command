#!/bin/zsh

set -u

script_dir="${0:A:h}"
python_bin="$script_dir/.venv/bin/python"

if [[ ! -x "$python_bin" ]]; then
  echo "尚未安装运行环境。请先在本工具目录执行："
  echo "python3 -m venv .venv"
  echo ".venv/bin/python -m pip install -r requirements.txt"
  echo
  read "reply?按回车键关闭窗口。"
  exit 2
fi

if (( $# > 0 )); then
  input_dir="$1"
else
  echo "请输入或拖入要处理的业务系统文件夹，然后按回车："
  read -r input_dir
  input_dir="${input_dir#\'}"
  input_dir="${input_dir%\'}"
  input_dir="${input_dir#\"}"
  input_dir="${input_dir%\"}"
fi

if [[ ! -d "$input_dir" ]]; then
  echo "文件夹不存在：$input_dir"
  read "reply?按回车键关闭窗口。"
  exit 2
fi

"$python_bin" "$script_dir/parse_folder.py" "$input_dir"
exit_code=$?

echo
if (( exit_code == 0 )); then
  echo "处理完成。"
else
  echo "处理结束，但存在需要查看的失败项目。详情见输出目录中的 文件分析.json。"
fi
read "reply?按回车键关闭窗口。"
exit "$exit_code"
