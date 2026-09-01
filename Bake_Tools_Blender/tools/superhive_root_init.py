"""Blender add-on entry point for the Superhive distribution channel."""


bl_info = {
    "name": "Bake Groups Tool",
    "author": "Veteraros AI",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Bake Tools",
    "description": "Prepare HP/LP bake groups, cages, smoothing and FBX exports",
    "category": "3D View",
}

from .addon import bake_tools_blender as _implementation


def register():
    _implementation.register()


def unregister():
    _implementation.unregister()
