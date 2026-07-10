__all__ = ['ValveSource_CollectionProps']

import bpy
from bpy.props import BoolProperty, IntProperty, CollectionProperty
from ..utils import get_id
from .mixins import ExportableProps
from .items import ArmatureItemEntry


class ValveSource_CollectionProps(ExportableProps, bpy.types.PropertyGroup):
    mute : BoolProperty(name=get_id("group_suppress"), description=get_id("group_suppress_tip"), default=False)
    bypass : BoolProperty(name=get_id("group_bypass"), description=get_id("group_bypass_tip"), default=False)
    selected_item : IntProperty(default=-1, max=-1, min=-1)
    automerge : BoolProperty(name=get_id("group_merge_mech"), description=get_id("group_merge_mech_tip"), default=False)
    export_object_entries : CollectionProperty(type=ArmatureItemEntry)
