"""Custom icon library.

Every .png in `icons/` is loaded at register() and addressed by its filename
stem, so adding an icon means dropping a file in - no code change:

    from .icons import icon
    layout.label(text="Jigglebones", icon_value=icon('jigglebone'))

`icon()` returns 0 for an unknown name, which Blender renders as no icon, so a
missing or failed file degrades to a plain label instead of raising.
"""

import bpy, os
import bpy.utils.previews

ICONS_DIR = os.path.join(os.path.dirname(__file__), "icons")

_previews = None


def icon(name: str) -> int:
    """icon_value for icons/<name>.png, or 0 if it isn't loaded."""
    if _previews is None:
        return 0
    entry = _previews.get(name)
    return entry.icon_id if entry else 0


def names() -> list:
    """Loaded icon names, for enum items and debugging."""
    return sorted(_previews.keys()) if _previews else []


def register():
    global _previews
    if _previews is not None:
        return
    _previews = bpy.utils.previews.new()
    if not os.path.isdir(ICONS_DIR):
        return
    for filename in sorted(os.listdir(ICONS_DIR)):
        stem, ext = os.path.splitext(filename)
        if ext.lower() != ".png":
            continue
        try:
            _previews.load(stem, os.path.join(ICONS_DIR, filename), 'IMAGE')
        except Exception as err:
            print("KitsuneSourceTools: could not load icon {} ({})".format(filename, err))


def unregister():
    global _previews
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
