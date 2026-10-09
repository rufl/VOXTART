# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""VOXTART voxel-first authoring tools.

The first implementation stores a versioned sparse voxel asset in Blender
custom properties while the data model and mesher remain independent of bpy.
This is intentionally a prototype boundary for the later native data-block.
"""

bl_info = {
    "name": "VOXTART Voxel Authoring",
    "author": "VOXTART contributors",
    "version": (0, 1, 0),
    "blender": (5, 2, 0),
    "description": "Voxel-first kitbashing primitives and deterministic meshing",
    "location": "View3D > Sidebar > VOXTART",
    "category": "3D View",
}

def register():
    from . import operators
    operators.register()


def unregister():
    from . import operators
    operators.unregister()
