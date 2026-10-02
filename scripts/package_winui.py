"""Build an application-local Windows x64 ZIP; run with a host Python with pip."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.13.15"
PYTHON_SHA256 = "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf"
DEPENDENCIES = ["numpy==2.5.3", "manifold3d==3.5.3", "Pillow==12.3.0"]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from litmetica3d import __version__ as VERSION
NAME = f"Litematica3D-WinUI-v{VERSION}-win-x64"

def run(*args, **kwargs):
    subprocess.run(args, check=True, cwd=ROOT, **kwargs)

def build(destination, runtime_from=None, no_restore=False):
    destination.mkdir(parents=True, exist_ok=True)
    package = destination / NAME
    # Refuse to overwrite an existing build or accidentally delete user files.
    package.mkdir()
    runtime = package / "runtime" / "python"
    if runtime_from:
        source = runtime_from.resolve()
        info_path = source / "build-info.json"
        if not info_path.is_file():
            raise RuntimeError(f"Runtime 缺少 build-info.json：{source}")
        info = json.loads(info_path.read_text(encoding="utf-8"))
        if info.get("python") != PYTHON_VERSION or info.get("dependencies") != DEPENDENCIES or info.get("architecture") != "win-x64":
            raise RuntimeError("复用的便携运行时版本或依赖不匹配。")
        shutil.copytree(source / "runtime" / "python", runtime)
    else:
        cache = ROOT / "work" / "portable-downloads"
        cache.mkdir(parents=True, exist_ok=True)
        archive = cache / f"python-{PYTHON_VERSION}-embed-amd64.zip"
        if not archive.exists():
            urllib.request.urlretrieve(f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{archive.name}", archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != PYTHON_SHA256:
            raise RuntimeError("Python archive SHA-256 mismatch")
        runtime.mkdir(parents=True)
        with zipfile.ZipFile(archive) as z:
            z.extractall(runtime)
        (runtime / "python313._pth").write_text("python313.zip\n.\nLib\\site-packages\n..\\..\\\n", encoding="ascii")
        run(sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:",
            "--platform", "win_amd64", "--python-version", "3.13", "--implementation", "cp", "--abi", "cp313",
            "--no-compile", "--target", str(runtime / "Lib" / "site-packages"), *DEPENDENCIES)
    publish_args = ["dotnet", "publish", "frontend/Litmetica3D.WinUI/Litmetica3D.WinUI.csproj",
        "-c", "Release", "-p:Platform=x64", "-o", str(package), "-p:DebugType=None", "-p:DebugSymbols=false",
        f"-p:OutputPath={ROOT / 'work' / 'portable-compiled'}{os.sep}"]
    if no_restore:
        publish_args.append("--no-restore")
    run(*publish_args)
    # Unpackaged WinUI publish omits the application's generated PRI/XBF files.
    # Ship the matching compiled resources as well as the published assemblies.
    compiled = ROOT / "work" / "portable-compiled"
    for resource in ("Litmetica3D.WinUI.pri", "App.xbf", "MainWindow.xbf"):
        shutil.copy2(compiled / resource, package / resource)
    shutil.copytree(ROOT / "litmetica3d", package / "litmetica3d",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "LICENSE", package / "LICENSE")
    for document in ("README.md", f"RELEASE_NOTES_v{VERSION}.md", "ISSUE_2_VERIFICATION.md"):
        source = ROOT / document
        if source.is_file():
            shutil.copy2(source, package / document)
    # Keep runtime/package notices distributed by their vendors.
    notices = package / "licenses"
    notices.mkdir()
    nuget = Path(os.environ.get("NUGET_PACKAGES", str(Path.home() / ".nuget" / "packages")))
    for vendor, version in [("microsoft.windowsappsdk", "2.5.1"), ("microsoft.windowsappsdk.winui", "2.3.9")]:
        for filename in ("license.txt", "NOTICE.txt"):
            source = nuget / vendor / version / filename
            if source.exists():
                shutil.copy2(source, notices / f"{vendor}-{filename}")
    (package / "使用说明.txt").write_text("""Litematica 3D · WinUI 3 便携版
适用于 Windows 10 2004 或更新版本 / Windows 11，64 位 x64。

1. 先将 ZIP 完整解压到可写文件夹。
2. 双击 Litmetica3D.WinUI.exe。
3. 导入 .litematic 投影，选择保存位置和用途，点击开始转换。

无需安装 Python、.NET SDK、WinUI 或 Minecraft；正常转换无需联网。
请保留完整文件夹，不能只复制 EXE。支持含中文和空格的目录。
“转换引擎”中的 Python 路径留空即可使用随包环境。
设置保存在用户 LocalAppData/Litmetica3D/winui-settings.json。

输出保存到所选目录/L3D_output/投影名/，已有同名目录会自动编号，不覆盖旧结果。
转换报告默认只在界面显示；需要时可在“日志”页面手动另存 JSON。
强制取消原生计算后可能留下 .litmetica3d-* 临时文件夹。

作者：b站@ZZHaccount
项目及许可：见 LICENSE。
源码：https://github.com/zzhaccount1121/litmetica3d
WinUI 原始贡献：https://github.com/sitbat/litmetica3d/tree/feat/winui3-frontend
Python 官方运行时：https://www.python.org/downloads/release/python-31315/
Python 许可位于 runtime/python/LICENSE.txt；依赖许可保留在其 dist-info 中。
.NET 及 Windows App SDK 许可保留在发布目录和 licenses 中。
""", encoding="utf-8-sig")
    (package / "build-info.json").write_text(json.dumps({
        "version": VERSION, "python": PYTHON_VERSION, "python_archive_sha256": PYTHON_SHA256,
        "dependencies": DEPENDENCIES, "architecture": "win-x64"
    }, indent=2), encoding="utf-8")
    run(str(runtime / "python.exe"), "-c",
        "import numpy, PIL, manifold3d, litmetica3d.winui_bridge; print('Bundled engine imports OK')")
    zip_path = Path(shutil.make_archive(str(destination / NAME), "zip", destination, NAME))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_suffix(".zip.sha256").write_text(f"{digest}  {zip_path.name}\n", encoding="ascii")
    print(f"ZIP: {zip_path}\nSHA256: {digest}", flush=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "release" / "winui-portable")
    parser.add_argument("--reuse-runtime", type=Path, help="复用已验证且依赖版本完全匹配的便携包 Python runtime 目录")
    parser.add_argument("--no-restore", action="store_true", help="使用现有 NuGet assets，适用于离线或已完成还原的构建机")
    args = parser.parse_args()
    build(args.output.resolve(), args.reuse_runtime, args.no_restore)
