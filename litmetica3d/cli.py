"""Command line interface for the shared Litematica 3D conversion engine."""

import argparse
import pathlib
import sys

from .conversion import ConversionOptions, convert
from . import __version__
from .litematic import load_schematic_info, load_schematic


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="litmetica3d",
        description="将 Litematica 投影转换为 STL 或 OBJ",
    )
    parser.add_argument("input", nargs="?")
    parser.add_argument("output", nargs="?")
    parser.add_argument("-f", "--format", choices=["stl", "obj"])
    parser.add_argument("--stl-mode", choices=["binary", "ascii"], default="binary")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--assets", help="模型资源目录或Minecraft JAR")
    parser.add_argument("--jar", dest="assets", help=argparse.SUPPRESS)
    parser.add_argument("--water", choices=["cube", "drop", "level"], default="cube")
    parser.add_argument("--fallback", choices=["cube", "ignore"], default="cube")
    parser.add_argument("--minimum-thickness", type=float, default=1 / 16)
    parser.add_argument("--geometry", choices=["print", "visual"], default="print")
    parser.add_argument(
        "--components", choices=["keep", "remove-small", "main"], default="keep"
    )
    parser.add_argument(
        "--min-component-volume", type=float, default=1 / 4096
    )
    parser.add_argument(
        "--cavities", choices=["preserve", "fill"], default="preserve"
    )
    parser.add_argument(
        "--boolean-fallback", choices=["voxel32", "fail"], default="voxel32"
    )
    parser.add_argument("-r", "--region", action="append", default=[])
    parser.add_argument("--center", action="store_true")
    parser.add_argument("--color", action="store_true")
    parser.add_argument("--seamless-glass", action="store_true", help="视觉模式使用半透明无缝玻璃")
    parser.add_argument("--solid-textures", action="store_true", help="特殊优化：填平贴图镂空，保留原贴图，显著减少像素几何")
    parser.add_argument(
        "--textures", action=argparse.BooleanOptionalAction, default=True,
        help="视觉 OBJ 使用原版纹理（STL 始终不包含纹理）",
    )
    parser.add_argument(
        "--emission", action=argparse.BooleanOptionalAction, default=True,
        help="Visual OBJ exports pixel emission masks and Blender Cycles setup",
    )
    parser.add_argument("--emission-strength", type=float, default=1.0)
    parser.add_argument("--emission-config")
    parser.add_argument(
        "--blender-lights",
        choices=["none", "off", "material", "exact", "clustered"],
        default="exact",
        help=(
            "Visual OBJ lighting: none, material only, exact editable lights, "
            "or clustered editable lights (off is a legacy alias for material)"
        ),
    )
    parser.add_argument("--info", action="store_true")
    parser.add_argument("--list-regions", action="store_true")
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--version", action="version", version=f"litmetica3d {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.gui:
        from .desktop import launch_gui
        return launch_gui()
    if not args.input:
        parser.print_help()
        return 1
    source = pathlib.Path(args.input)
    if not source.exists():
        parser.error(f"文件不存在: {source}")
    if args.info:
        info = load_schematic_info(str(source))
        print(f"名称: {info.name}\n作者: {info.author}\n数据版本: {info.data_version}")
        return 0
    if args.list_regions:
        for name in load_schematic_info(str(source)).regions:
            print(name)
        return 0
    if not args.output:
        parser.error("必须提供输出文件")
    output = pathlib.Path(args.output)
    fmt = args.format or output.suffix.lower().lstrip(".")
    if fmt not in {"stl", "obj"}:
        parser.error("输出格式必须是 stl 或 obj")
    try:
        report = convert(
            ConversionOptions(
                input_path=source,
                output_path=output,
                asset_path=pathlib.Path(args.assets) if args.assets else None,
                output_format=fmt,
                stl_binary=args.stl_mode == "binary",
                scale=args.scale,
                center=args.center,
                water=args.water,
                fallback=args.fallback,
                minimum_thickness=args.minimum_thickness,
                regions=tuple(args.region),
                color=args.color,
                textures=args.textures,
                geometry=args.geometry,
                components=args.components,
                min_component_volume=args.min_component_volume,
                cavities=args.cavities,
                boolean_fallback=args.boolean_fallback,
                emission=args.emission,
                seamless_glass=args.seamless_glass,
                solid_textures=args.solid_textures,
                emission_strength=args.emission_strength,
                emission_config=(
                    pathlib.Path(args.emission_config)
                    if args.emission_config else None
                ),
                blender_lights=args.blender_lights,
            ),
            lambda stage, value, text: print(f"[{value:>6.1%}] {text}"),
        )
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    print(
        f"完成: {report.triangles} 三角形，"
        f"实体模型 {report.entity_model_blocks}，"
        f"回落立方体 {report.fallback_cubes}，忽略 {report.ignored}，"
        f"跳过水体 {report.water_skipped}"
    )
    if report.solid:
        print(
            f"可打印: {'是' if report.solid.printable else '否'}，"
            f"独立壳体 {report.solid.component_count}，"
            f"最终保留 {report.solid.retained_component_count}，"
            f"封闭空腔 {report.solid.cavity_count}"
        )
    return 0
