import bpy, sys, math
from pathlib import Path
from mathutils import Vector
sys.path.insert(0, r"C:\Users\rafael\VOXTART\scripts\addons_core")
import voxtart
voxtart.register()
from voxtart import operators
blend = r"C:\Users\rafael\Downloads\VOXTART_pixel_character.blend"
out = Path(r"C:\Users\rafael\Downloads\voxtart_rotation_frames")
out.mkdir(exist_ok=True)
bpy.ops.wm.open_mainfile(filepath=blend)
obj = next(o for o in bpy.data.objects if o.get(operators.ASSET_PROPERTY))
# Center the asset without changing its voxel coordinates.
asset = operators._load_asset(obj)
if asset.voxels:
    xs = [c[0] for c in asset.voxels]; ys = [c[1] for c in asset.voxels]; zs = [c[2] for c in asset.voxels]
    center = Vector(((min(xs)+max(xs))*0.5, (min(ys)+max(ys))*0.5, (min(zs)+max(zs))*0.5))
else:
    center = Vector((0,0,0))
obj.location = -center
obj.rotation_mode = 'XYZ'
scene = bpy.context.scene
scene.render.engine = 'BLENDER_EEVEE'
scene.render.resolution_x = 480; scene.render.resolution_y = 480; scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.film_transparent = False
scene.world.color = (0.025, 0.04, 0.035)
# Camera
cam_data = bpy.data.cameras.new('VOXTART Rotation Camera'); cam = bpy.data.objects.new('VOXTART Rotation Camera', cam_data); scene.collection.objects.link(cam)
cam.location = (0, -420, 105); cam.rotation_euler = (Vector((0,0,0)) - cam.location).to_track_quat('-Z','Y').to_euler(); cam.data.type='ORTHO'; cam.data.ortho_scale=205; scene.camera=cam
# Lighting
ld = bpy.data.lights.new('VOXTART Key', 'AREA'); ld.energy=1200; ld.shape='DISK'; ld.size=220
lo = bpy.data.objects.new('VOXTART Key',ld); scene.collection.objects.link(lo); lo.location=(80,-180,300); lo.rotation_euler=(Vector((0,0,70))-lo.location).to_track_quat('-Z','Y').to_euler()
ld2=bpy.data.lights.new('VOXTART Fill','AREA'); ld2.energy=500; ld2.size=180
lo2=bpy.data.objects.new('VOXTART Fill',ld2); scene.collection.objects.link(lo2); lo2.location=(-220,-80,120); lo2.rotation_euler=(Vector((0,0,70))-lo2.location).to_track_quat('-Z','Y').to_euler()
for frame in range(24):
    obj.rotation_euler[2] = math.tau * frame / 24.0
    scene.render.filepath = str(out / f'frame_{frame:03d}.png')
    bpy.ops.render.render(write_still=True)
print(f'VOXTART_RENDER_FRAMES {out}')
