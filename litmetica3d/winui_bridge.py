"""UTF-8 JSON-lines transport for the native WinUI desktop application.

One request per process; subsequent stdin lines may contain {"command":"cancel"}.
stdout is protocol-only. Engine diagnostics are redirected to stderr.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading


def emit(kind, **data):
    print(json.dumps({"type": kind, **data}, ensure_ascii=False), file=sys.__stdout__, flush=True)


def run(request, cancelled, send=emit):
    from .conversion import ConversionCancelled, ConversionOptions, convert
    from .output_layout import next_model_path, normalize_output_root

    files = [Path(p).resolve() for p in request.get("files", [])]
    if not files:
        raise ValueError("请至少选择一个 .litematic 文件。")
    output_text = request.get("output_dir", "").strip()
    if not output_text:
        raise ValueError("请选择输出文件夹。")
    output = normalize_output_root(Path(output_text).resolve())
    options = dict(request.get("options", {}))
    for reserved in ("input_path", "output_path", "save_report"):
        if reserved in options:
            raise ValueError(f"不允许的参数：{reserved}")
    choices = {
        "output_format": ("stl", "obj"), "geometry": ("print", "visual"),
        "water": ("cube", "drop", "level"), "fallback": ("cube", "ignore"),
        "optimize": ("raw", "safe", "experimental"),
        "components": ("keep", "remove-small", "main"),
        "cavities": ("preserve", "fill"), "boolean_fallback": ("voxel32", "fail"),
        "blender_lights": ("none", "material", "exact", "clustered"),
    }
    for key, values in choices.items():
        if key in options and options[key] not in values:
            raise ValueError(f"无效参数：{key}")
    for key in ("scale", "minimum_thickness", "min_component_volume", "emission_strength"):
        if key in options:
            value = float(options[key])
            if not math.isfinite(value) or value < 0 or (key in ("scale", "minimum_thickness") and value == 0):
                raise ValueError(f"无效数值：{key}")
            options[key] = value
    fmt = options.get("output_format", "stl")
    if fmt == "stl":
        options["geometry"] = "print"
    visual = options.get("geometry", "print") == "visual"
    options["textures"] = visual
    if not visual:
        options.update(emission=False, blender_lights="none", color=False, seamless_glass=False)
    elif options.get("blender_lights") == "none":
        options["emission"] = False
    for key in ("asset_path", "emission_config"):
        if options.get(key):
            options[key] = Path(options[key])
        else:
            options.pop(key, None)
    options["regions"] = tuple(options.get("regions", ()))
    if len(files) > 1 and options["regions"]:
        raise ValueError("区域筛选仅适用于单个投影；批量转换请清空区域筛选。")
    names = set()
    for source in files:
        if not source.is_file() or source.suffix.lower() != ".litematic":
            raise ValueError(f"无效投影文件：{source}")
        key = source.stem.casefold()
        if key in names:
            raise ValueError(f"批量任务中存在同名投影：{source.stem}")
        names.add(key)
    output.mkdir(parents=True, exist_ok=True)
    reserved = set()
    destinations = [next_model_path(output, source, fmt, reserved) for source in files]
    for index, source in enumerate(files):
        if cancelled():
            raise ConversionCancelled()
        destination = destinations[index].parent
        send("log", text=f"[{index + 1}/{len(files)}] {source.name}")
        # Publish the model set (model, materials, textures) only on success.
        with tempfile.TemporaryDirectory(prefix=".litmetica3d-", dir=output) as scratch:
            stage = Path(scratch) / source.stem
            stage.mkdir()
            def progress(step, value, text):
                send("progress", stage=step, value=(index + value) / len(files), text=text)
            with redirect_stdout(sys.stderr):
                report = convert(ConversionOptions(
                    input_path=source, output_path=stage / f"{source.stem}.{fmt}",
                    **options,
                ), progress, cancelled)
            if cancelled():
                raise ConversionCancelled()
            data = asdict(report)
            # Atomic directory rename cannot overwrite an existing Windows
            # result. A competing publication gets a fresh reserved name.
            while True:
                try:
                    stage.rename(destination)
                    break
                except FileExistsError:
                    destinations[index] = next_model_path(output, source, fmt, reserved)
                    destination = destinations[index].parent
            data["output_path"] = str(destinations[index])
        send("report", report=data)
    send("complete", text=f"已完成 {len(files)} 个投影的转换。")


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    cancel = threading.Event()
    def lines():
        # Avoid holding BufferedReader's lock in a daemon during interpreter exit.
        pending = bytearray()
        while chunk := os.read(sys.stdin.fileno(), 1):
            if chunk == b"\n":
                yield pending.decode("utf-8")
                pending.clear()
            else:
                pending.extend(chunk)
        if pending:
            yield pending.decode("utf-8")
    incoming = lines()
    try:
        # Load native NumPy/Manifold DLLs before a thread blocks in CRT stdin.
        # Windows DLL initialization can otherwise contend on the CRT I/O lock.
        from .conversion import ConversionCancelled
        request = json.loads(next(incoming))
        def listen():
            for line in incoming:
                try:
                    if json.loads(line).get("command") == "cancel":
                        cancel.set()
                except (ValueError, AttributeError):
                    pass
            # Parent exited or closed the pipe: don't leave an orphan conversion.
            cancel.set()
        threading.Thread(target=listen, daemon=True).start()
        run(request, cancel.is_set)
        return 0
    except Exception as exc:
        from .conversion import ConversionCancelled
        if isinstance(exc, ConversionCancelled):
            emit("cancelled", text="转换已取消；已完成的投影保留。")
            return 0
        emit("error", text=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
