"""Run unchanged lighting operations in short Blender main-thread batches."""


def responsive_setup(script):
    script = script.replace('import json\n', 'import json\nimport time\nfrom functools import lru_cache\n', 1)
    script = script.replace('DATA = json.loads(', '''if bpy.app.driver_namespace.get("_l3d_setup_running"):
    raise RuntimeError("导入正在运行，请等待或按 Esc 取消后再运行。")
DATA = json.loads(''', 1)
    script = script.replace('scene = bpy.context.scene\n', '''SETUP_TEXT_PATH = getattr(getattr(getattr(bpy.context, "space_data", None), "text", None), "filepath", "")
saved_viewports = []
for window in bpy.context.window_manager.windows:
    for area in window.screen.areas:
        if area.type == "VIEW_3D":
            shading = area.spaces.active.shading
            saved_viewports.append((shading, shading.type))
            if shading.type in {"RENDERED", "MATERIAL"}:
                shading.type = "SOLID"
scene = bpy.context.scene
''', 1)
    script = script.replace('def candidate_roots():', '@lru_cache(maxsize=1)\ndef candidate_roots():', 1)
    script = script.replace('def resolve_asset(relative_path):', '''def asset_roots(relative):
    cheap = []
    text = getattr(getattr(bpy.context, "space_data", None), "text", None)
    text_path = getattr(text, "filepath", "") if text is not None else ""
    if text_path:
        cheap.append(Path(bpy.path.abspath(text_path)).resolve().parent)
    source_path = globals().get("__file__", "")
    if source_path and Path(source_path).exists():
        cheap.append(Path(source_path).resolve().parent)
    cheap.append(OUTPUT_ROOT_HINT)
    for root in cheap:
        if (root / relative).is_file():
            yield root
            return
    yield from candidate_roots()

@lru_cache(maxsize=None)
def resolve_asset(relative_path):''', 1)
    script = script.replace('for root in candidate_roots():', 'for root in asset_roots(relative):', 1)
    script = script.replace('text_path = getattr(text, "filepath", "") if text is not None else ""',
                            'text_path = SETUP_TEXT_PATH')
    start = script.index('# Pixel-accurate surface emission.')
    end = script.index('print("Litematica emission and editable Cycles lights are ready.")', start)
    body = script[start:end]
    # Creating in an unlinked collection avoids a depsgraph rebuild after
    # every UI batch. Link once at the end, without changing final membership.
    body = body.replace('scene.collection.children.link(collection)',
                        'PENDING_COLLECTION = collection', 1)
    # A compositor loop used this helper's name as a global loop variable.
    # Inside a generator it would instead shadow the callable for all steps.
    body = body.replace('for socket in (glare.inputs', 'for input_socket in (glare.inputs')
    body = body.replace('list(socket.links)', 'list(input_socket.links)')
    body = body.replace('for item in DATA["materials"]:', '''for index, item in enumerate(DATA["materials"]):
    yield ("材质", index, len(DATA["materials"]), index)''', 1)
    body = body.replace('    for item in DATA.get("lights", []):', '''    for index, item in enumerate(DATA.get("lights", [])):
        yield ("光源", index, len(DATA.get("lights", [])), len(DATA["materials"]) + index)''', 1)
    body += '\nattach_pending_collection()\nyield ("完成", 1, 1, len(DATA["materials"]) + len(DATA.get("lights", [])))\n'
    body = '\n'.join('    ' + line if line else '' for line in body.splitlines())
    return script[:start] + 'def setup_steps():\n    global PENDING_COLLECTION\n' + body + '\n\n' + RUNNER


RUNNER = '''# bpy stays on the main thread; yield to UI after <=32 items or 20ms.
PENDING_COLLECTION = None

def attach_pending_collection():
    global PENDING_COLLECTION
    if PENDING_COLLECTION is not None:
        if PENDING_COLLECTION.name not in scene.collection.children:
            scene.collection.children.link(PENDING_COLLECTION)
        PENDING_COLLECTION = None

class SetupJob:
    def __init__(self):
        self.steps = setup_steps()
        self.started = time.perf_counter()
        self.last_log = -1.0
        self.done = False
        self.wm = bpy.context.window_manager
        self.workspace = bpy.context.workspace
        self.total = max(1, len(DATA["materials"]) + len(DATA.get("lights", [])))
        self.wm.progress_begin(0, self.total)
        bpy.app.driver_namespace["_l3d_setup_running"] = True
        print("Litematica 导入开始：材质 %d，光源 %d；Esc 取消。" %
              (len(DATA["materials"]), len(DATA.get("lights", []))), flush=True)

    def finish(self, cancelled=False, error=None):
        if self.done:
            return
        self.done = True
        self.steps.close()
        attach_pending_collection()
        self.wm.progress_end()
        if self.workspace:
            self.workspace.status_text_set(None)
        bpy.app.driver_namespace.pop("_l3d_setup_running", None)
        for shading, previous in saved_viewports:
            try:
                shading.type = previous
            except ReferenceError:
                pass
        message = "失败: " + str(error) if error else ("已取消，可重新运行继续" if cancelled else "完成")
        print("Litematica %s，耗时 %.2f 秒" % (message, time.perf_counter() - self.started), flush=True)

    def tick(self):
        deadline = time.perf_counter() + 0.02
        try:
            for _ in range(32):
                stage, current, count, completed = next(self.steps)
                self.wm.progress_update(completed)
                elapsed = time.perf_counter() - self.started
                message = "Litematica %s %d/%d · %.1f 秒 · Esc 取消" % (stage, current, count, elapsed)
                if self.workspace:
                    self.workspace.status_text_set(message)
                if elapsed - self.last_log >= 0.5 or stage == "完成":
                    print(message, flush=True)
                    self.last_log = elapsed
                if time.perf_counter() >= deadline:
                    break
        except StopIteration:
            self.finish()
        except Exception as error:
            self.finish(error=error)
            raise
        return self.done


class L3D_OT_setup_import(bpy.types.Operator):
    bl_idname = "wm.litematica_setup_import"
    bl_label = "Litematica 光源与材质导入"

    def execute(self, context):
        self.job = SetupJob()
        self.timer = context.window_manager.event_timer_add(0.02, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC":
            self.job.finish(cancelled=True)
            context.window_manager.event_timer_remove(self.timer)
            self.report({"WARNING"}, "已取消；保留已导入部分，重新运行可继续。")
            return {"CANCELLED"}
        if event.type == "TIMER":
            try:
                done = self.job.tick()
            except Exception as error:
                context.window_manager.event_timer_remove(self.timer)
                self.report({"ERROR"}, str(error))
                return {"CANCELLED"}
            if done:
                context.window_manager.event_timer_remove(self.timer)
                self.report({"INFO"}, "Litematica 导入完成。")
                return {"FINISHED"}
        return {"PASS_THROUGH"}

    def cancel(self, context):
        self.job.finish(cancelled=True)
        context.window_manager.event_timer_remove(self.timer)


if bpy.app.background or bpy.context.window is None:
    job = SetupJob()
    while not job.tick():
        pass
else:
    previous = getattr(bpy.types, "L3D_OT_setup_import", None)
    if previous is not None:
        bpy.utils.unregister_class(previous)
    bpy.utils.register_class(L3D_OT_setup_import)
    bpy.ops.wm.litematica_setup_import()
'''
