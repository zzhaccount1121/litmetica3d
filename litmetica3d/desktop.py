"""Desktop entry point: native WinUI on Windows, existing Qt elsewhere."""
from pathlib import Path
import subprocess
import sys


def launch_gui():
    # Existing PyInstaller/Qt distributions retain their embedded UI.
    if sys.platform != "win32" or getattr(sys, "frozen", False):
        from .gui_app import launch_gui as launch_qt
        return launch_qt()
    root = Path(__file__).resolve().parent.parent
    exe = root / "frontend/Litmetica3D.WinUI/bin/x64/Release/net10.0-windows10.0.26100.0/win-x64/Litmetica3D.WinUI.exe"
    if exe.is_file():
        try:
            return subprocess.call([str(exe)], cwd=root)
        except OSError as exc:
            print(f"WinUI 前端启动失败，改用 Qt 界面：{exc}", file=sys.stderr)
    from .gui_app import launch_gui as launch_qt
    return launch_qt()
