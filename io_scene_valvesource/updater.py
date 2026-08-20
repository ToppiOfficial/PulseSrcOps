import os, re, json, ssl, shutil, zipfile, tempfile, threading
import urllib.request, urllib.error
from bpy.types import Operator
from .utils import get_id, get_addon_prefs

REPO = "ToppiOfficial/PulseSrcOps"
_API_STABLE = "https://api.github.com/repos/" + REPO + "/releases/latest"
_API_DEV = "https://api.github.com/repos/" + REPO + "/releases/tags/dev"

class UpdateInfo:
    def __init__(self, channel: str, version: tuple, label: str, build_date: str, download_url: str, page_url: str):
        self.channel = channel
        self.version = version
        self.label = label
        self.build_date = build_date
        self.download_url = download_url
        self.page_url = page_url

_info: UpdateInfo | None = None
_error: str = ""
_restart_required: bool = False

def reset_state():
    global _info, _error
    _info = None
    _error = ""

def restart_required() -> bool:
    return _restart_required

def info_label() -> str:
    return _info.label if _info else ""

def _addon_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))

def _parse_version(s: str) -> tuple:
    m = re.search(r"(\d+(?:\.\d+)+)", s)
    return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)

def get_current_version() -> tuple:
    try:
        import tomllib
        with open(os.path.join(_addon_dir(), "blender_manifest.toml"), "rb") as f:
            return _parse_version(tomllib.load(f)["version"])
    except Exception:
        return (0,)

def _urlopen(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "PulseSrcOps-Updater", "Accept": "application/vnd.github+json"})
    try:
        return urllib.request.urlopen(req, timeout=30)
    except urllib.error.URLError as e:
        # Blender's Python may lack OS certs; retry with its bundled certifi.
        if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
            return urllib.request.urlopen(req, timeout=30, context=ctx)
        raise

def check_for_updates(channel: str):
    global _info, _error
    _info = None
    _error = ""
    url = _API_DEV if channel == 'DEV' else _API_STABLE
    try:
        with _urlopen(url) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _error = get_id("updater_no_release") if e.code == 404 else str(e)
        return
    except Exception as e:
        _error = str(e)
        return

    asset = next((a for a in data.get("assets", []) if a["name"].lower().endswith(".zip")), None)
    if asset is None:
        _error = get_id("updater_no_release")
        return

    build_date = asset.get("updated_at") or data.get("published_at") or ""
    if channel == 'DEV':
        version = _parse_version(asset["name"])
        label = "dev ({0})".format(build_date[:10])
    else:
        version = _parse_version(data.get("tag_name", ""))
        label = data.get("tag_name") or asset["name"]
    _info = UpdateInfo(channel, version, label, build_date, asset["browser_download_url"], data.get("html_url", "https://github.com/" + REPO + "/releases"))

def update_available() -> bool:
    if _info is None or _restart_required:
        return False
    if _info.version > get_current_version():
        return True
    if _info.channel == 'DEV':
        prefs = get_addon_prefs()
        return prefs is None or _info.build_date != prefs.dev_build_date
    return False

def _extract_and_replace(zip_path: str, stage: str):
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.startswith("/") or ".." in name.split("/"):
                raise RuntimeError("Unsafe path in update archive: " + name)
        zf.extractall(stage)

    # Manifest sits either at the archive root or in a single top-level folder.
    src = None
    if os.path.isfile(os.path.join(stage, "blender_manifest.toml")):
        src = stage
    else:
        for name in os.listdir(stage):
            d = os.path.join(stage, name)
            if os.path.isdir(d) and os.path.isfile(os.path.join(d, "blender_manifest.toml")):
                src = d
                break
    if src is None:
        raise RuntimeError("No blender_manifest.toml in update archive")

    target = _addon_dir()
    for name in os.listdir(target):
        p = os.path.join(target, name)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
    for name in os.listdir(src):
        shutil.move(os.path.join(src, name), os.path.join(target, name))

def install_update(info: UpdateInfo):
    global _restart_required
    tmp = tempfile.mkdtemp(prefix="pulsesrcops_update_")
    try:
        zip_path = os.path.join(tmp, "update.zip")
        with _urlopen(info.download_url) as r, open(zip_path, "wb") as f:
            shutil.copyfileobj(r, f)
        _extract_and_replace(zip_path, os.path.join(tmp, "stage"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    prefs = get_addon_prefs()
    if prefs is not None:
        prefs.dev_build_date = info.build_date if info.channel == 'DEV' else ""
    _restart_required = True

def _startup_check():
    prefs = get_addon_prefs()
    if prefs is None or not prefs.update_auto_check:
        return None
    channel = prefs.update_channel
    threading.Thread(target=lambda: check_for_updates(channel), daemon=True).start()
    return None

class SMD_OT_CheckForUpdates(Operator):
    bl_idname = "smd.check_for_updates"
    bl_label = get_id("updater_check")
    bl_description = get_id("updater_check_tip")
    bl_options = {'INTERNAL'}

    def execute(self, context):
        prefs = get_addon_prefs()
        check_for_updates(prefs.update_channel if prefs else 'STABLE')
        if _error:
            self.report({'ERROR'}, get_id("updater_error", True).format(_error))
            return {'CANCELLED'}
        assert _info
        if update_available():
            self.report({'INFO'}, get_id("updater_available", True).format(_info.label))
        else:
            self.report({'INFO'}, get_id("updater_up_to_date", True).format(".".join(map(str, get_current_version()))))
        return {'FINISHED'}

class SMD_OT_InstallUpdate(Operator):
    bl_idname = "smd.install_update"
    bl_label = get_id("updater_install_label")
    bl_description = get_id("updater_install_tip")
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context) -> bool:
        return _info is not None and not _restart_required

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        global _error
        assert _info
        try:
            install_update(_info)
        except Exception as e:
            _error = str(e)
            self.report({'ERROR'}, get_id("updater_error", True).format(_error))
            return {'CANCELLED'}
        self.report({'INFO'}, get_id("updater_restart"))
        return {'FINISHED'}

def draw_notice(layout):
    """One-line update notice for panels; draws nothing when there is no news."""
    if _restart_required:
        row = layout.row()
        row.alert = True
        row.label(text=get_id("updater_restart"), icon='INFO')
    elif update_available():
        assert _info
        row = layout.row()
        row.alert = True
        row.operator(SMD_OT_InstallUpdate.bl_idname, text=get_id("updater_available", True).format(_info.label), icon='IMPORT')

def draw_prefs(layout, prefs):
    header = layout.row(align=True)
    header.prop(prefs, "show_updater",
        text=get_id("updater_title"),
        icon='DISCLOSURE_TRI_DOWN' if prefs.show_updater else 'DISCLOSURE_TRI_RIGHT',
        emboss=False)

    # Keep news visible while collapsed.
    if not prefs.show_updater:
        if _restart_required:
            sub = header.row()
            sub.alert = True
            sub.label(text=get_id("updater_restart"), icon='INFO')
        elif update_available():
            assert _info
            sub = header.row()
            sub.alert = True
            sub.label(text=get_id("updater_available", True).format(_info.label), icon='IMPORT')
        return

    col = layout.column()
    col.prop(prefs, "update_auto_check")

    row = col.row(align=True)
    row.prop(prefs, "update_channel", expand=True)

    row = col.row(align=True)
    row.operator(SMD_OT_CheckForUpdates.bl_idname, icon='FILE_REFRESH')
    if _info:
        row.operator("wm.url_open", text=get_id("updater_view"), icon='URL').url = _info.page_url

    if _restart_required:
        row = col.row()
        row.alert = True
        row.label(text=get_id("updater_restart"), icon='INFO')
    elif _error:
        row = col.row()
        row.alert = True
        row.label(text=get_id("updater_error", True).format(_error), icon='ERROR')
    elif _info:
        if update_available():
            row = col.row()
            row.scale_y = 1.2
            row.alert = True
            row.operator(SMD_OT_InstallUpdate.bl_idname, text=get_id("updater_install", True).format(_info.label), icon='IMPORT')
        else:
            col.label(text=get_id("updater_up_to_date", True).format(".".join(map(str, get_current_version()))), icon='CHECKMARK')
            col.operator(SMD_OT_InstallUpdate.bl_idname, text=get_id("updater_reinstall", True).format(_info.label))
