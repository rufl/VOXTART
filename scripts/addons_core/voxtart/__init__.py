# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""VOXTART voxel-first authoring tools.

The first implementation stores a versioned sparse voxel asset in Blender
custom properties while the data model and mesher remain independent of bpy.
This is intentionally a prototype boundary for the later native data-block.
"""

bl_info = {
    "name": "VOXTART",
    "author": "VOXTART contributors",
    "version": (0, 18, 0),
    "blender": (5, 2, 0),
    "description": "Goxel-inspired voxel authoring with dynamic mesh voxelization, pixel tiling and proportional spritesheet projection, calibrated image-to-voxel reconstruction, Bloformer volume deformation, portable VOX/QB/Blockbench/Minecraft formats, layered SpriteStack/PSD/ORA/KRA imports rendered as voxels, stack manifests, categorized Zoo Gallery asset browsing, favorites, thumbnails, surface/grid/vertex placement, brush scattering, path distribution, transform randomization, collision and material overrides, blob gallery kitbashing, socketed voxel character editing, V-Optimizer-style greedy/marching mesh cleanup, lasso blobs, skeleton retargeting, game-rig extraction, action baking, palettes, layers, and optional MCP plugins",
    "location": "VOXTART desktop launcher; View3D > Sidebar > Item / VOXTART",
    "category": "3D View",
}

def register():
    from . import animation, blob_gallery_ui, character_editor_ui, desktop_shell, desktop_ui, bloformer_ui, export_ui, formats_ui, goxel_ui, layers_ui, lasso_blob, mcp, minecraft_ui, operators, optimizer_ui, palette_ui, reconstruct_ui, scene_hierarchy_ui, shading_ui, skeleton_wizard, spritesheet_export_ui, stacking_ui, tiler_ui, ux, viewport_workflow_ui, voxel_editor
    operators.register()
    formats_ui.register()
    export_ui.register()
    spritesheet_export_ui.register()
    palette_ui.register()
    goxel_ui.register()
    voxel_editor.register()
    viewport_workflow_ui.register()
    layers_ui.register()
    tiler_ui.register()
    reconstruct_ui.register()
    lasso_blob.register()
    bloformer_ui.register()
    ux.register()
    desktop_ui.register()
    desktop_shell.register()
    animation.register()
    skeleton_wizard.register()
    blob_gallery_ui.register()
    character_editor_ui.register()
    optimizer_ui.register()
    scene_hierarchy_ui.register()
    stacking_ui.register()
    minecraft_ui.register()
    shading_ui.register()
    mcp.register()


def unregister():
    from . import animation, blob_gallery_ui, character_editor_ui, desktop_shell, desktop_ui, bloformer_ui, export_ui, formats_ui, goxel_ui, layers_ui, lasso_blob, mcp, minecraft_ui, operators, optimizer_ui, palette_ui, reconstruct_ui, scene_hierarchy_ui, shading_ui, skeleton_wizard, spritesheet_export_ui, stacking_ui, tiler_ui, ux, viewport_workflow_ui, voxel_editor
    mcp.unregister()
    shading_ui.unregister()
    minecraft_ui.unregister()
    stacking_ui.unregister()
    scene_hierarchy_ui.unregister()
    optimizer_ui.unregister()
    character_editor_ui.unregister()
    blob_gallery_ui.unregister()
    skeleton_wizard.unregister()
    animation.unregister()
    desktop_shell.unregister()
    desktop_ui.unregister()
    ux.unregister()
    bloformer_ui.unregister()
    lasso_blob.unregister()
    reconstruct_ui.unregister()
    tiler_ui.unregister()
    viewport_workflow_ui.unregister()
    voxel_editor.unregister()
    goxel_ui.unregister()
    palette_ui.unregister()
    spritesheet_export_ui.unregister()
    export_ui.unregister()
    formats_ui.unregister()
    operators.unregister()
