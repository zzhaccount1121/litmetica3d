"""Native-client protocol tests using real, minimal gzip NBT schematics."""
import gzip
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from litmetica3d.winui_bridge import run
from litmetica3d.conversion import ConversionCancelled


def write_fixture(path, block="minecraft:stone"):
    def string(s):
        raw = s.encode("utf-8")
        return struct.pack(">H", len(raw)) + raw
    def tag(kind, name, value):
        return bytes([kind]) + string(name) + value
    def integer(name, value):
        return tag(3, name, struct.pack(">i", value))
    def compound(name, payload):
        return tag(10, name, payload + b"\0")
    position = compound("Position", b"".join(integer(a, 0) for a in "xyz"))
    size = compound("Size", b"".join(integer(a, 1) for a in "xyz"))
    palette = tag(9, "BlockStatePalette", b"\x0a" + struct.pack(">i", 2) +
                  tag(8, "Name", string("minecraft:air")) + b"\0" + tag(8, "Name", string(block)) + b"\0")
    states = tag(12, "BlockStates", struct.pack(">iq", 1, 1))
    data = compound("", integer("Version", 6) + integer("MinecraftDataVersion", 3955) +
                    compound("Regions", compound("测试区域", position + size + palette + states)))
    path.write_bytes(gzip.compress(data))


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "中文 demo.litematic"
        write_fixture(self.source)
        self.output = self.root / "models"
        self.request = {"files": [str(self.source)], "output_dir": str(self.output),
                        "options": {"output_format": "stl", "geometry": "print"}}
        self.events = []

    def tearDown(self):
        self.temp.cleanup()

    def send(self, kind, **data):
        self.events.append({"type": kind, **data})

    def test_real_stl_is_water_tight_and_report_points_to_final_location(self):
        run(self.request, lambda: False, self.send)
        report = next(e["report"] for e in self.events if e["type"] == "report")
        model = Path(report["output_path"])
        self.assertTrue(model.exists())
        triangles = struct.unpack("<I", model.read_bytes()[80:84])[0]
        self.assertEqual(12, triangles)
        self.assertTrue(report["solid"]["printable"])
        self.assertFalse(model.with_suffix(".report.json").exists())
        expected_model = self.output / "L3D_output" / self.source.stem / model.name
        self.assertEqual(expected_model.resolve(strict=True), model.resolve(strict=True))
        self.assertFalse(list(self.output.glob(".litmetica3d-*")))
        self.assertEqual("complete", self.events[-1]["type"])

    def test_visual_obj_publishes_referenced_materials_and_textures(self):
        self.request["options"] = {"output_format": "obj", "geometry": "visual", "blender_lights": "material"}
        run(self.request, lambda: False, self.send)
        folder = self.output / "L3D_output" / self.source.stem
        model = folder / f"{self.source.stem}.obj"
        material = next(line[7:] for line in model.read_text(encoding="utf-8").splitlines() if line.startswith("mtllib "))
        mtl = folder / material
        self.assertTrue(mtl.exists())
        maps = [line[7:] for line in mtl.read_text(encoding="utf-8").splitlines() if line.startswith("map_Kd ")]
        self.assertTrue(maps)
        for texture in maps:
            self.assertTrue((folder / texture).exists(), texture)

    def test_existing_output_gets_numbered_folder_without_overwrite(self):
        target = self.output / "L3D_output" / self.source.stem
        target.mkdir(parents=True)
        sentinel = target / "keep.txt"
        sentinel.write_text("original")
        run(self.request, lambda: False, self.send)
        self.assertEqual("original", sentinel.read_text())
        report = next(e["report"] for e in self.events if e["type"] == "report")
        expected_model = (self.output / "L3D_output" / f"{self.source.stem} (2)"
                          / f"{self.source.stem}.stl")
        self.assertEqual(expected_model.resolve(strict=True),
                         Path(report["output_path"]).resolve(strict=True))

    def test_cancel_removes_unpublished_files(self):
        def interrupted(options, progress, cancelled):
            options.output_path.write_text("partial")
            raise ConversionCancelled()
        with patch("litmetica3d.conversion.convert", interrupted), self.assertRaises(ConversionCancelled):
            run(self.request, lambda: False, self.send)
        self.assertFalse((self.output / "L3D_output" / self.source.stem).exists())

    def test_duplicate_names_rejected_for_entire_batch(self):
        folder = self.root / "second"
        folder.mkdir()
        second = folder / self.source.name
        write_fixture(second)
        self.request["files"].append(str(second))
        with self.assertRaisesRegex(ValueError, "同名"):
            run(self.request, lambda: False, self.send)
        self.assertFalse(self.output.exists())

    def test_invalid_numbers_rejected(self):
        self.request["options"]["scale"] = float("nan")
        with self.assertRaises(ValueError):
            run(self.request, lambda: False, self.send)

    def test_real_subprocess_protocol_and_clean_exit(self):
        with subprocess.Popen([sys.executable, "-u", "-m", "litmetica3d.winui_bridge"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8") as process:
            process.stdin.write(json.dumps(self.request, ensure_ascii=False) + "\n")
            process.stdin.flush()
            messages = []
            for line in process.stdout:
                item = json.loads(line)
                messages.append(item)
            self.assertEqual(0, process.wait(timeout=10), process.stderr.read())
            self.assertEqual("complete", messages[-1]["type"])
            self.assertTrue(any(x["type"] == "progress" for x in messages))
            self.assertTrue(any(x["type"] == "report" for x in messages))

    def test_cancel_request_over_stdin(self):
        with subprocess.Popen([sys.executable, "-u", "-m", "litmetica3d.winui_bridge"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8") as process:
            process.stdin.write(json.dumps(self.request) + '\n{"command":"cancel"}\n')
            process.stdin.flush()
            messages = [json.loads(line) for line in process.stdout]
            self.assertEqual(0, process.wait(timeout=10), process.stderr.read())
            self.assertEqual("cancelled", messages[-1]["type"])
            self.assertFalse((self.output / "L3D_output" / self.source.stem).exists())


if __name__ == "__main__":
    unittest.main()
