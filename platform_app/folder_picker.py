from __future__ import annotations

import subprocess
import sys
from pathlib import Path


class FolderPickerError(RuntimeError):
    pass


def choose_system_folder() -> Path | None:
    """Open the local OS folder picker and return an absolute directory path."""
    if sys.platform == "darwin":
        script = (
            'try\n'
            'POSIX path of (choose folder with prompt "请选择一个业务系统文件夹")\n'
            'on error number -128\n'
            'return "__CANCELLED__"\n'
            'end try'
        )
        completed = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        selected = completed.stdout.strip()
        if selected == "__CANCELLED__":
            return None
        if completed.returncode != 0:
            raise FolderPickerError(completed.stderr.strip() or "无法打开文件夹选择窗口")
    else:
        try:
            from tkinter import Tk, filedialog

            root = Tk()
            root.withdraw()
            selected = filedialog.askdirectory(title="请选择一个业务系统文件夹")
            root.destroy()
        except Exception as error:  # pragma: no cover - macOS is the primary target
            raise FolderPickerError(f"无法打开文件夹选择窗口：{error}") from error
        if not selected:
            return None

    path = Path(selected).expanduser().resolve()
    if not path.is_dir():
        raise FolderPickerError(f"选择的路径不是有效文件夹：{path}")
    return path
