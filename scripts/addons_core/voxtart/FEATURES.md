<!-- SPDX-FileCopyrightText: 2026 Blender Authors -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# VOXTART

VOXTART is a full-featured desktop voxel editor. The current desktop runtime
bundles Blender as its geometry, rendering, import/export, and animation
engine, while VOXTART owns the editor workflow and primary UI.

The deterministic voxel model and codec logic remain ordinary Python modules
so they can be validated without Blender. The bundled runtime is an
implementation detail of the current desktop build, not an add-on installation
requirement for users.

## Getting started

For the current Windows development build:

```text
run_voxtart.cmd
```

The launcher starts a clean bundled Blender runtime, enables VOXTART
automatically, hides the auxiliary Blender editors behind a full-screen 3D
viewport, and configures the active workspace as **VOXTART**. The short VoxStart
splash then opens the voxel editor directly on the starter asset instead of
making a new user choose a project shell first.
The default editor surface hides Blender's header, native toolbar, sidebar,
gizmos, grid, axes, footer, and status bar. VOXTART supplies its own compact
top bar with **Project**, **Save**, **Details**, and **More tools** controls.
**More tools** is the explicit escape hatch for Blender's native controls;
**Details** reveals technical hit, layer, selection, and shortcut diagnostics
only when needed.
Each new unsaved project opens with an editable light-yellow stepped pyramid:
6x6 voxels on the base, followed by 4x4 and 2x2 layers. It is selected and
framed with breathing room in the viewport so the user can immediately place,
erase, paint, or select voxels with the VOXTART editor.
The starter asset also seeds a compact eight-color palette with the light-yellow swatch selected, so the visible strip is useful before any palette setup.
The first editor session shows a small **Try this** card with plain-language
mouse instructions, a visible **Colors** strip, and only the beginner actions:
**Add block**, **Erase block**, **Color block**, and **Select blocks**. The card
disappears after the first changed edit, which also reports the action and its
block count with an undo reminder.
The desktop shell remains available as a secondary project surface through the
editor's **Project** control. It uses a project-editor layout: project tab and
modes at the top, project items on the left, palette and assets on the right,
and a compact status bar at the bottom. When opened before the first edit, it
defaults to **Build**, keeps only the starter-focused project category, and
uses the same beginner tool labels.

### Core project loop

The P1 authoring loop is intentionally complete before advanced workflows:

1. **Make Blocks** creates a starter asset from the project surface.
2. **Open voxel editor** enters the product editor with the new asset active.
3. The visible **Colors** strip selects a palette entry for add and paint.
4. Face clicks add, paint, or erase voxels; **Select blocks** selects a hit
   voxel, `Ctrl+C` copies it, and `Ctrl+D` clears the selection. `Ctrl+Z` and
   `Ctrl+Y` undo and redo the committed stroke.
5. **Save** writes the Blender project, and reopening it restores the asset,
   palette indices, recovery state, and clipboard state.
6. **Project** or `Esc` returns to the VOXTART project surface.


### Build pipeline

The P1 build path stays inside the project surface: select a source mesh, use
**Voxelize selected** to create a linked sparse asset, use **Refresh linked**
after source edits, then use **Optimize active** before opening it in the
editor. The generated asset keeps the source relationship and optimizer report
while the source mesh remains available.
The portable `.voxel.json` path is also closed for P1: exporting the active
authoritative asset and importing it as a new asset preserves its sparse voxels
and palette data.
The Build surface's **Export mesh** action closes the P1 handoff to a
material-aware optimized OBJ, while keeping the authoritative voxel asset
unchanged and recording the export report.
The project surface's **Import VOX / QB** action closes P1 native voxel
interchange; the release gate round-trips VOX while the same operator retains
Qubicle QB support.
The Character surface's **Prepare rig**, **Import animation**, **Extract game
rig**, **Bake actions**, **Preview action**, and **Export game rig** controls
close the native character-animation handoff. Prepare creates an editable
blockout armature and binds the active mesh; Extract creates a compact target
armature; the native baker maps source actions onto that target, and Export
writes a GLB while leaving the source mesh available for further edits.
Optional external retargeting add-ons, manual mapping, and Rigify fitting
remain advanced paths.
The palette panel's **Import palette** and **Reduce palette** actions close
local palette-file intake and deterministic reduction. Import loads a palette
into the visible Colors strip and, when enforcement is on, retargets the active
asset to the imported colors. Reduce preserves occupied cells while recording
the weighted median-cut report and resulting palette size.
Remote palette sources and the broader palette-library workflow remain advanced
paths.
Rounded controls expose direct paths for creating, importing, voxelizing,
refreshing, painting, optimizing, exporting, and character work after the
first-run path; hovering a control shows its label and available shortcut.
On Windows, the host window is branded **VoxTart** and uses the voxel-cake
icon at `assets/voxtart.ico`.
The launcher suppresses Blender's native splash through an isolated preferences
directory, then displays the custom **VoxStart 0.0.1-dogfood-(build)** splash
with the transparent-background icon before opening the voxel editor.
The packaged `VoxTart.exe` launcher embeds the same transparent icon and keeps
the Blender host window hidden until its native title is already **VoxTart**,
so the Blender version title is never exposed during startup.
The launcher also runs `tools/brand_voxtart_runtime.py` before startup, patching
the bundled PE title format and native icon resource before Blender can create
its first window.

The package can still be registered directly inside Blender for development
and native testing, but the product entrypoint is the VOXTART desktop launcher.
VOXTART stores sparse voxels, palette colors, sockets, provenance, and optional
layer data in versioned JSON/custom properties.

VOXTART does not bundle third-party art libraries or claim ownership of
imported models, palettes, textures, or animation files. Verify the source
license before redistributing an exported asset.

## Authoring tools

### Asset model and palette

- `VoxelAsset` stores integer grid cells, a normalized RGBA palette, grid
  spacing, named sockets, and provenance.
- Stable JSON serialization supports scene custom properties and portable
  files.
- Palette tools accept HEX/TXT, GIMP GPL, JASC PAL, JSON, and Adobe ASE
  data. The advanced Palette Library can import one of the listed free
  palette sources by its explicit source URL.
- Palette operations add/remove colors, remap affected voxels to the nearest
  target color, select an editing swatch, and apply a scene palette.

### Goxel-style voxel editing

The VOXTART tool shelf and Brush panel expose grid-aligned editing:

- add, remove, and paint individual cells;
- brush radius and circular/disc sampling;
- flood fill inside explicit bounds;
- component selection and wire overlays;
- ray-picked cell and face normal;
- symmetry and picked-cell quick actions;
- protected-cell overlays and edit reports;
- greedy or marching surface rebuilds.

`VoxelEditTransaction` makes each edit atomic. Exceptions restore the exact
pre-edit asset. Protected cells are skipped unless the operation explicitly
enables protected editing.

The editor keeps Blender's middle-mouse orbit, pan, and wheel zoom behavior
available while adding a compact voxel workflow:

- `Tab` switches between **Edit** and **Scene** workspaces;
- the first-run toolbar uses readable **Add block**, **Erase block**, **Color
  block**, and **Select blocks** labels before showing their shortcut letters;
- a visible **Colors** strip selects the active palette swatch without opening
  Blender's sidebar;
- advanced brushes and selection categories appear after the first changed edit;
- the default overlay stays lightweight: the asset, block count, current mode,
  one short guidance card, palette, and edit status;
- **Details** reveals hit, layer, selection, and shortcut diagnostics on demand;
- `Esc` returns to the VOXTART project surface rather than exposing a raw
  Blender workspace;
- the compact VOXTART toolbar keeps project tools one click away; Blender's
  native toolbar, sidebar, gizmos, grid, and axes stay hidden until **More
  tools** is selected;
- the full Adobe-style vertical toolbar groups advanced brush and selection
  tools, highlights the active tool, and exposes a tooltip on hover;
- the VOXTART sidebar exposes a Color Picker, active-layer controls, session
  History, and a Scene Hierarchy inspector after native controls are revealed.
- In **Add** or **Attach**, **left click places** on a voxel face and **right
  click erases** the hit voxel; dragging paints continuously. When the ray
  misses the asset, Add/Attach places on the ground grid plane so the first
  voxel does not require a pre-existing block;
- `B/T/E/P/S` switch Add, Attach, Erase, Paint, and Select modes;
- `V/L/O/C/F` switch Voxel, Line, Box, Sphere, and face-oriented brushes;
- face brushes expose oriented radius, depth, hollow, erase, and paint behavior;
- `J/K/M/N/R` select Lasso, Circle, Color, Component, and Surface tools;
- `G/R/M` move, rotate, or mirror selected voxels; `Shift+D` duplicates a
  selection; arrows/PageUp/PageDown adjust the transform and `Enter` commits;
- drag strokes mutate the active layer in memory, redraw at most 60 Hz, and
  rebuild the Blender mesh once on release;
- a low-contrast, viewport-only wire seam traces occupied voxel edges so
  adjacent cells remain visually legible without changing the rendered asset;
- `1/2/3` toggles X/Y/Z mirroring and the viewport draws the mirror guides;
- translucent brush previews, hovered-face outlines, hit normals, active mode,
  selection counts, active-layer lock state, and stroke change counts remain
  visible in the 3D View overlay;
- `Home` recenters the view; `NumPad1`, `NumPad3`, and `NumPad7` align front,
  right, and top views;
- `Alt` plus click picks a palette color from the hit voxel face;
- Select plus Box/Lasso/Circle supports occupied-cell marquee selection;
  `Shift` adds and `Ctrl` subtracts from the current selection;
- `Ctrl+A/C/X/V` selects all, copies, cuts, and pastes voxel selections;
- `Ctrl+D` clears the current selection;
- `Ctrl+Z` and `Ctrl+Shift+Z`/`Ctrl+Y` restore per-stroke editor history;
- `[` and `]` change brush radius, `H` toggles hollow stamping, and `Esc`
  cancels an unfinished stroke or returns to the VOXTART project surface.

The editor writes through `VoxelEditTransaction`, so protected cells,
active-layer ownership, sparse storage, and deterministic mesh rebuilds remain
consistent with the existing VOXTART operators. Interrupted strokes are
autosaved to the active asset and recovered on the next editor session.
Clipboard selections persist on the asset and scene, with palette-index
validation before paste.

Layer and object surface controls keep the sparse voxel source intact while
choosing the display mesh backend. **Layer Round** stores a marching-surface
preference on the active layer; **Object Round** overrides the whole selected
asset. **Use Object/Layer Surface** clears the override, so different scene
objects can use greedy blocks or smooth rounded marching surfaces independently.
The **View & Cameras** panel supports multiple scene cameras. Each camera can
be made the render camera, focused in the main viewport, soloed while the
others are disabled in the viewport, or restored with **Show All**. Camera
viewport and render visibility remain separate Blender object properties.
Open the editor directly from the VOXTART launcher, from the shell's **Open
voxel editor** action, or from the global `Alt+Shift+Q` shortcut. Use
**Details** for diagnostic hit/layer information and **More tools** when
opening Blender's native sidebar is intentional.

### Dynamic voxelizer

The **Dynamic Voxelizer** converts a source mesh at the current voxel grid
size, samples its nearest source material into the active palette, and stores
the source relationship on the generated VOXTART asset:

- `SOLID` keeps the full interior volume;
- `SURFACE` keeps only the outer one-voxel surface;
- `SHELL` keeps a configurable outer shell up to 64 voxels thick;
- **Refresh Linked** re-voxelizes the stored source mesh after it changes;
- grid-volume budgets prevent accidental unbounded searches.

Refresh intentionally rebuilds the linked asset's base layer from the source
mesh. The source object remains editable; the generated VOXTART object retains
the selected mode, shell thickness, grid size, and source name as metadata.

### Volume conversion tools

VOXTART includes clean-room sparse-volume operations modeled on the useful
conversion workflow documented by vengi:

- **Mirror** reflects occupied cells and socket normals around the occupied
  bounding-box midpoint.
- **Rotate** turns volumes by 90°, 180°, or 270° on any axis while keeping
  rectangular results on the integer voxel lattice.
- **Translate** moves cells and sockets by an integer voxel offset.
- **Crop** keeps a bounded region and can rebase its minimum cell to the local
  origin while the Blender operator compensates the object transform.
- `split_asset()` partitions large sparse assets into deterministic local
  chunks keyed by their world-space chunk origins.
- Every Blender transform applies to all layer voxels, preserves grid spacing,
  sockets, palette indices, and records a JSON transform report.

The Palette Library also provides weighted deterministic **median-cut palette
reduction**. It uses voxel frequency as the color weight, preserves occupancy
and sockets, and records the reduction method and resulting palette size.

### Lasso Blob

**Lasso Blob** turns a 2D polygon into a rounded sparse volume. Depth,
palette index, symmetry axis, and marching/block conversion are available in
the panel. Polygon validation, finite coordinates, voxel budgets, and positive
depth limits prevent unbounded allocations.

### Bloformer

**Bloformer** applies a bounded primitive to the active layer without
requiring a separate sculpt mesh.

- Modes: `ADD`, `SUBTRACT`, `CARVE`, `INFLATE`, `ERODE`, `SMOOTH`.
- Shapes: `SPHERE`, `BOX`, `CYLINDER`.
- Cylinder axes: `X`, `Y`, `Z`.
- Controls: center, radius, shell thickness, palette index, overwrite, and
  protected-cell permission.
- Edits are layer-aware and atomic. The operator stores a JSON `EditReport`
  in `voxtart_bloformer_report` and `voxtart_edit_report`.
- The volume safety budget is two million candidate cells.

`CARVE` removes outside shell layers of the primitive; `SUBTRACT` removes the
whole primitive. `INFLATE`, `ERODE`, and `SMOOTH` only affect cells inside the
selected primitive.


### Pixel Tiler

The **Pixel Tiler** panel brings a spritesheet workflow into the voxel
editor without depending on a third-party tiling implementation:

- choose an image, tile width/height, and row-major tile index;
- apply the tile to selected mesh faces as closest-filtered, repeating UVs;
- project the tile on the `X`, `Y`, or `Z` voxel plane;
- choose positive/negative side, extrusion depth, rotation, and flips;
- use `STAMP` to preserve transparent pixels or `REPLACE` to clear the tile
  footprint first;
- ignore pixels below the configurable alpha threshold;
- stamp into the active unlocked layer or convert a tile into a standalone
  voxel asset.

The source can also be switched to **Generated** for internal procedural
tiles. `SOLID`, `CHECKER`, `STRIPES`, `NOISE`, and `BRICK` patterns use the
configured colors, repeat counts, and deterministic seed. The generator
matches every opposite border exactly, so generated textures can repeat
without seams. The same pixels feed mesh UV projection, active-layer stamping,
and the **Convert Tile to New Voxel Asset** action. The deterministic API is
`generate_seamless_texture`, `generate_seamless_tile`, and
`generate_seamless_voxel_asset`.

Pixel scale is not an independent tiler setting. The panel uses the current
editor `voxtart_grid_size`, and the operation requires it to match the active
asset `grid_size`. Therefore one source texture pixel always occupies one
voxel cell and exactly that many scene units. Atlas UV rectangles and reported
world extents are derived from source pixel dimensions, so changing scale
cannot silently stretch or compress a tile.

### Calibrated multi-view reconstruction

The deterministic reconstruction core accepts one to six indexed
orthographic views through `voxtart.reconstruct`:

- `OrthographicView` records axis, direction, top-left origin, pixel rows, and
  bounded integer depth;
- transparent pixels become empty-ray constraints and indexed pixels become
  occupied-ray constraints;
- `SURFACE` selects the nearest viable cell, while `VOLUME` retains every
  viable cell;
- cells that remain possible but unselected are returned as an explicit
  unresolved set rather than being silently guessed;
- deterministic render-back metrics report missing, extra, conflicting, and
  color-mismatched pixels;
- source view names, count, and depth mode are preserved in asset provenance.

The converter is intentionally indexed and orthographic; arbitrary perspective
inference is not part of this path. The Blender adapter exposes the same
calibration contract without changing the authoritative voxel core.

### Image and palette intake

`voxtart.image_intake` converts calibrated RGBA rows into indexed source views
without resampling:

- source dimensions and nearest-neighbor pixel identity are retained;
- fully transparent pixels can become background through an alpha threshold;
- `NONE`, `ALPHA`, `CORNER`, and `EXPLICIT` background policies are explicit;
- palettes are extracted by deterministic frequency ordering with
  lexicographic tie breaks across one frame or a shared multi-view set, or
  accepted from the caller;
- bounded palettes map overflow colors to the nearest stable entry;
- each indexed image records dimensions, background settings, palette size, and
  an SHA-256 digest for reconstruction provenance;
- `IndexedImage.to_view` applies axis, facing, origin, integer depth, and
  priority calibration before calling `voxtart.reconstruct`.


`voxtart.reconstruct_ui` loads Blender-supported image files, creates a new
non-destructive voxel asset, stores the source/calibration/report metadata, and
draws unresolved candidate cells as a purple wire overlay. The single-view
panel handles one source directly; the multi-view panel accepts up to six
independently calibrated images and applies one shared palette before solving
the constraints. Both panels report unresolved, conflicting, missing, extra,
and color-mismatched pixels.

The core remains offline-capable and testable with deterministic RGBA fixtures;
file-format decoding is delegated to Blender's image loader at the adapter
boundary.


### Animated voxel spritesheet export

The **Spritesheet Export** panel renders the active voxel object at sampled
scene frames and writes static PNG tiles for 2D runtimes.

- use an explicitly selected Blender action or the active animation action;
- sample an inclusive start/end range with a configurable step;
- render front, back, left, right, top, or isometric orthographic views;
- auto-fit one stable camera to the evaluated bounds across all sampled frames;
- pack frames row-major with configurable columns, spacing, and padding;
- keep transparent backgrounds and optionally retain the generated Blender
  image datablock;
- write a `.json` sidecar containing atlas dimensions and each scene-frame
  rectangle.

The exporter temporarily isolates the voxel object and its armature
dependencies, restores the scene frame/camera/render state afterward, and
stores the export report on the source object.

### Non-destructive layers

The Layers panel manages a bounded `LayerStack`:

- create, duplicate, rename, delete, move, merge-down, and flatten;
- visibility, lock, opacity, and blend mode;
- blend modes `NORMAL`, `ADD`, `SUBTRACT`, `MULTIPLY`, and `SCREEN`;
- deterministic JSON persistence in `voxtart_layers_json`;
- palette remapping across every layer;
- composite the visible stack for mesh rebuild and export.

The active layer is the edit target for Bloformer, shading, dithering, and
layer-aware operators. Locked layers are not mutated.

### Shading and dithering

The Voxel Shading panel changes palette indices while preserving occupancy.
Surface normals, ambient/directional lighting, quantized levels, group size,
protected cells, and selected-cell filtering are supported. Dither patterns
are `CHECKER`, `BAYER2`, `BAYER4`, `ORDERED`, `CIRCUIT`, and `NOISE`, with
`XY`, `XZ`, and `YZ` axes, Bayer sizes, density, and deterministic noise.
Dither is represented as a non-destructive layer when used through Blender.

## Interchange and export

### Native voxel formats

- MagicaVoxel `.vox`, including multiple `SIZE`/`XYZI` models and palette
  handling.
- Qubicle Binary `.qb`, compressed and uncompressed streams, with RGBA and
  BGRA color formats.
- Optimized mesh output from greedy or marching voxel surfaces with vertex
  welding, degenerate/duplicate removal, triangulation, and material
  compaction.
- Blender mesh export paths for OBJ, GLB/GLTF, FBX, and STL when the matching
  Blender exporter is installed.

### Voxel optimizer

The **Voxel Optimizer** panel is a clean-room, Blender-native workflow inspired
by the public [V-Optimizer reference](https://vailor1.itch.io/v-optimizer) and
its documented meshing/export feature set:

- **Greedy** meshing keeps adjacent same-material voxel surfaces as quads;
- **Marching Cubes** produces a smooth triangle surface for downstream tools;
- vertex welding, degenerate-face removal, duplicate-face removal, optional
  triangulation, and material compaction are independently configurable;
- applying the operation replaces only the active object's display mesh,
  preserves the sparse voxel source, and stores a deterministic count/reduction
  report on the object;
- the optimized mesh remains available to Blender's installed OBJ, GLB/GLTF,
  FBX, and STL exporters.

The current panel intentionally does not claim V-Optimizer's textured greedy
mesher, MagicaVoxel/Goxel/Kenshape CLI, or external Godot scene exporter. Those
are separate format/tooling contracts from this add-on's source-preserving
Blender workflow.

### Blockbench

The Blockbench panel imports and exports generic cube-based `.bbmodel`
files. The project shell exposes a direct **Import Blockbench** shortcut,
while the full panel provides the matching export workflow. Import supports
cube elements, rotated elements, mesh bounds, indexed or explicit colors,
visibility/export flags, and Bedrock `minecraft:geometry` bone cubes. Export
writes deterministic merged voxel runs, UUIDs, palette metadata, and grid
size.

### Minecraft

The Minecraft panel imports and exports:

- Sponge schematic (`.schem`);
- legacy MCEdit schematic (`.schematic`);
- structure NBT (`.nbt`);
- Anvil regions (`.mca`) and world directories.

The project shell exposes a direct **Import Minecraft** shortcut; the full
panel provides the matching export workflow. The codec includes raw/gzip/zlib
NBT, palettes and block properties, packed section indices, legacy blocks,
region chunk tables, and world-origin preservation. Air is sparse and
transparent palette colors map to a selected default block on export.

### SpriteStack and layered images

The SpriteStack / Layered Voxels panel imports JSON slice manifests, image
sequences, SpriteStack-style VOX slices, OpenRaster (`.ora`), PSD, Krita
(`.kra`) PNG-backed layers, and layered image documents available through
Blender. The project shell exposes a direct **Import layered stack** shortcut;
the full panel provides rebuild and portable manifest export. Stack settings
control axis (`X`, `Y`, `Z`), bottom/top ordering, alpha threshold, voxel
scale, palette limit, and rebuild behavior. A stack can be exported as a
portable `.sstack.json` manifest.

PSD decoding supports common 8-bit and 16-bit RGB/greyscale channels,
raw/RLE/ZIP channel compression, visibility, opacity, offsets, and layer
names. OpenRaster reads `stack.xml` offsets and visibility.

## Gallery, joints, and scene assembly

### Blob Gallery / Zoo

The local Blob Gallery stores validated `.voxtart.blob.json` documents. The
project shell exposes a direct **Save active to Zoo** shortcut; the full Zoo
Gallery panel provides the matching search, insert, placement, and sharing
workflows. Each entry contains the authoritative asset, metadata, category,
tags, provenance, and named joints.

- save, load, import, export, remove, search, and category filtering;
- favorite flags stored separately from portable asset documents;
- bounded file sizes and path-safe IDs;
- insert a blob or save the active/selected scene assets;
- place a category on a grid;
- paint/scatter instances in a disc;
- distribute instances along a curve path;
- snap a blob joint to another asset or an armature bone;
- collision presets and material overrides;
- persist and restore transforms through `SceneAssembly` JSON.

Placement modes are `FREE`, `GRID`, `SURFACE`, `VERTEX`, `BRUSH`, and `PATH`.
Placement variation uses deterministic seeds for position, rotation, and scale.

### Scene hierarchy window

The **Scene Hierarchy** panel in the VOXTART 3D View sidebar shows placed
VOXTART assets as a deterministic collection/object tree. The project shell
exposes a direct **Sync scene hierarchy** action; the sidebar provides the
matching tree, filtering, selection, visibility, and inspector workflows.

- collection nesting is preserved;
- Blender asset parenting is shown beneath the parent asset;
- placement names and categories come from the saved `SceneAssembly`;
- clicking an asset row selects it and makes it active;
- each asset row exposes its viewport visibility toggle;
- search filters by placement name, object name, category, or placement ID;
- **Unplaced** includes VOXTART objects that are not yet recorded in the Zoo
  scene assembly;
- the refresh control synchronizes current object transforms before rebuilding
  the hierarchy.
- the inspector beside the tree shows the active object's transform, parent,
  asset/layer counts, active layer, and effective greedy/marching surface;

### Joints and kitbash

`VoxelSocket` records a grid position, axis normal, tag, and right-angle
rotation. Kitbash matching requires opposite normals and prefers exact tags,
then wildcard tags, then the shortest grid translation. Blob joints add names,
roles, and optional target bone names without changing the authoritative
socket contract.

### Voxel Character Editor

The **Voxel Character Editor** assembles a reusable character recipe from
Blob Gallery parts instead of baking one monolithic mesh:

- choose a target slot (`Root`, `Torso`, `Head`, left/right arms, left/right
  legs, or `Accessory`) and filter the local gallery;
- assign a gallery blob or save the active voxel asset into the gallery and
  assign it in one step;
- rebuild the character deterministically from its versioned scene recipe;
- mount each child through compatible opposite-facing voxel joints, with an
  explicit auto-snap toggle;
- keep each part as an independently editable, parented VOXTART object and
  replace or remove non-root slots without losing the saved recipe;
- clear the generated character and its recipe without affecting unrelated
  scene objects.

The editor requires a `Root` part before socketed children. A child slot must
have its canonical parent (`Torso` mounts to `Root`; head, limbs, and
accessories mount to `Torso`) and compatible joints when auto-snap is enabled.
The portable model is implemented by `character_editor.py` and serialized as
`voxtart.character` JSON in the scene.

## Character and animation workflow

- Optional animation-library import accepts user-owned `.blend`, `.fbx`,
  `.glb`, and `.gltf` sources through Blender importers.
- Humanoid bone-role normalization and one-to-one mapping suggestions are
  deterministic and do not import optional retargeting add-ons.
- The character preparation workflow creates an editable blockout rig for an
  unrigged mesh and can bind it with Blender automatic weights.
- Native, MWNI, and optional retarget backends are exposed when available.
- Game-rig extraction selects deform bones, deform-plus-selected bones, or
  all bones and can preserve or flatten missing hierarchy parents.
- Action selection supports one requested action, the active action, or all
  available actions.

### Uploaded skeleton smoke test

The supplied `Ninja_Cg.zip` and `Sukuna Model.zip` archives were inspected
without modifying or redistributing their contents. The deterministic
bone-role probe recognized representative root, pelvis, chest, head, arm,
forearm, hand, thigh, shin, and foot names from the Ninja hierarchy as
`13/13` expected humanoid roles. Mapping Ninja node names to Sukuna node names
produced `33` one-to-one suggestions.

The archive metadata also matters: Ninja contains animated Collada scene nodes
but no Collada skin/controller markers, while Sukuna contains animated DAE and
glTF node data but the glTF has `0` skins. Blender 5.2.2's native glTF import
produced `40` objects and `158` actions with `0` armatures for Sukuna. The
same runtime did not expose a Collada importer for Ninja, so its native DAE
result remains unverified; the static probe confirms it is a node hierarchy,
not a skinned/controller-backed asset.

Optional add-ons and external importers remain optional; VOXTART reports
availability instead of silently pretending an integration is installed.

## MCP bridge

The MCP panel starts a local JSON-RPC bridge. The dependency-free protocol
layer exposes:

- `initialize`, discovery, ping, logging, resources, and prompts;
- scene and asset inspection;
- bounded box/explicit asset creation;
- bounded add/remove/paint/fill/flood edits;
- mesh rebuild;
- animation plugin status and state inspection;
- optional animation-library import, character preparation, and retargeting requests;
- color, file, and request-size limits are validated before Blender operations.
Transport errors are returned as JSON-RPC errors and expected tool failures as
`isError` tool results.

## Verification and coverage

The repository includes standard-library tests for deterministic core behavior,
Blender-native behavior, a headless end-to-end editor matrix, and Windows GUI
probes:

```text
python tests/python/voxtart_core_test.py
blender --background --factory-startup --python tests/python/voxtart_blender_test.py
blender --background --factory-startup --python tests/python/voxtart_e2e_blender.py
python tests/python/voxtart_coverage.py --all-missing
blender --background --python tests/python/voxtart_blender_coverage.py
python -m compileall -q scripts/addons_core/voxtart tests/python tools
```

On Windows, the complete runner also exercises the compiled launcher:

```text
python tests/python/voxtart_e2e.py
tools\run_voxtart_e2e.cmd
```

`voxtart_e2e.py` runs the core suite, the Blender-native suite, the four-test
headless editor matrix, a save/reopen persistence probe, a background launcher
probe, and a visible `VoxTart.exe` GUI probe. The persistence probe saves a
`.blend` in one Blender process and recovers the interrupted stroke and
clipboard in a second process. The GUI probe invokes the real operator in a
`VIEW_3D`, verifies both draw handlers, and validates a rendered screenshot
containing the editor overlay and brush preview. Use `--skip-launcher` only
when the compiled Windows launcher is unavailable.

The current local regression baseline is 86 deterministic core tests, 24
Blender-native tests, and 4 additional end-to-end Blender tests. The E2E
performance check covers a 32³ asset, mesh rebuild, connected/flat/lasso/circle
selection, and overlapping layer composition.

`voxtart_coverage.py` and `voxtart_blender_coverage.py` use `sys.settrace`;
neither requires pytest or a third-party coverage package. The GitHub Actions
workflow runs the deterministic, Blender-native, and headless E2E suites on
Linux, then builds the Windows runtime and launcher, runs the complete Windows
P0 matrix with an isolated `VOXTART_CONFIG`, and uploads the GUI screenshot
and marker as workflow artifacts.
