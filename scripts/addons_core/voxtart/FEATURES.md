<!-- SPDX-FileCopyrightText: 2026 VOXTART contributors -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

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
viewport, and configures the active workspace as **VOXTART**. The short VoxTart
splash then opens the voxel editor directly on the starter asset instead of
making a new user choose a project shell first.
The default editor surface hides Blender's header, native toolbar, sidebar,
gizmos, grid, axes, footer, and status bar. VOXTART supplies its own friendly
top bar with **Undo**, **Redo**, **Home**, **Save**, **Help**, **More**,
**Menu**, and **Left**/**Right** drawer toggles; the last two keep workspace
panels available without consuming the canvas when they are closed.
**Menu** groups common **File**, **Edit**, **View**, **Tools**, and **Help**
commands in one discoverable surface without removing the quick actions above. **Undo** and **Redo** explain
their `Ctrl+Z` and `Ctrl+Y` shortcuts; **More** opens a small menu: **Grid**
draws a bounded 3D box around the active asset, with one line per voxel
interval, and `G` toggles it directly (`Shift+G` keeps the Select- or Scene-mode
move transform); **Hints** shows helpful
**Advanced tools** expands VOXTART's own tool strip. In Edit, the vertical
**TOOLS** shelf reserves its own lane before the left drawer instead of covering
panel content. Every visible panel has a draggable header: the **TOOLS** shelf,
**VIEWPORT** HUD, **TOOL OPTIONS** card, **ASK VOXTART** prompt, and the
**Colors**, **Layers**, and **History** drawers. Drawer panels still snap to the
nearest side when released; floating panels keep their new position.
Hovering a **More** or **Menu** row opens the same compact action hint with
its description and shortcut when one exists.
Blender's native chrome remains hidden throughout the product surface. The
editor starts with four
simple tools — **Add block**, **Erase block**, **Color block**, and
**Select blocks** — and keeps them visible after the first edit instead of
expanding the toolbar automatically. Custom POST_PIXEL controls follow the
Windows display scale, capped at 135%, so labels, buttons, and palette swatches
stay comfortable on high-DPI displays while narrow regions scale down their
layout to remain inside the viewport.
The top bar identifies the active **MAKE** or **PLACE** workspace with a
color-coded pill and the `Tab` shortcut.
Hovering a top-bar or vertical-toolbar control opens a compact action hint;
keyboard shortcuts stay visible when available.
The visual theme uses restrained near-black chrome, cool slate panels, blue
focus states, and green, amber, or red only for semantic feedback.

The custom overlays use the bundled Blender **Inter** font when the packaged
runtime exposes it, then fall back to Blender's default font without breaking
startup. Blender lists Inter under the SIL Open Font License 1.1; no new binary
font asset is added to VOXTART. Controls use original procedural line icons and
the existing CC0 icon artwork, while hover tips pair a plain-language action,
one short explanation, and its shortcut in a wrapped, high-contrast card.
Each new unsaved project opens with an editable light-yellow stepped pyramid:
6x6 voxels on the base, followed by 4x4 and 2x2 layers. It is selected and
framed with breathing room in the viewport so the user can immediately place,
erase, paint, or select voxels with the VOXTART editor.
The starter asset also seeds a compact eight-color palette with the light-yellow swatch selected, so the visible strip is useful before any palette setup.
The first editor session starts in a minimal state: only the beginner actions
— **Add block**, **Erase block**, **Color block**, and **Select blocks** — and
the essential Colors drawer are enabled. There is no onboarding card or wizard.
The minimal state ends after the first changed edit, which also reports the
action and its block count with an undo reminder. The top-bar **Help** guide
can reopen the same basics later, including how to orbit, undo, and redo a
change.
The editor top bar, color strip, toolbar, and floating panels share rounded
panels and accent borders so the editing surface reads as one focused product
workspace.

The editor keeps navigation discoverable with a compact **VIEWPORT** HUD beside
the Colors drawer. Its isometric cube and direct buttons expose **Front**,
**Right**, **Top**, **Frame**, and a projection toggle; `Numpad 1/3/7/5` and
`Home` provide the same camera shortcuts without opening Blender's native UI.
Hovering a **VIEWPORT** control opens a short orientation hint with its
keyboard shortcut.

The editor also keeps a contextual **TOOL OPTIONS** card beside the tool shelf,
following the brush-panel pattern used by dedicated voxel editors. It names the
active mode and brush, shows the matching shortcuts, and exposes click targets
for brush size, hollow stamping, mirror axes, face depth, and cylinder axis or
height. When **Select** is active, the same card becomes a selection context:
it reports the selected count and exposes **All**, **Clear**, **Copy**, and
**Cut** actions alongside `Ctrl+A`, `Ctrl+D`, `Ctrl+C`, and `Ctrl+X`. These
controls use
the same scene settings and selection state as the keyboard workflow, so the
compact beginner surface stays discoverable without revealing Blender's native
sidebar.
Hovering a control in the **TOOL OPTIONS** card opens a short action hint with
its description and keyboard shortcut when one exists; controls without a
dedicated shortcut still explain the mouse action.

In the **PLACE** workspace, the same contextual card becomes **SCENE OPTIONS**.
It exposes **Move**, **Rotate**, and **Scale**, shows `G · R · S`, and keeps the
arrow-key/Enter/Esc transform contract visible without requiring Blender's native
transform gizmos.

While a transform is active, **Apply** and **Cancel** buttons appear in the same
card, so the mouse path can commit or revert the change without relying on the
keyboard. Hovering any Scene control opens a short action hint with its shortcut,
including `Enter` for **Apply** and `Esc` for **Cancel**.

The desktop shell remains available as a secondary project surface through the
editor's **Home** control. It uses a project-editor layout: project tab and
modes at the top, project items on the left, palette and assets on the right,
and a compact status bar at the bottom. When opened before the first edit, it
defaults to **Build**, keeps only the starter-focused project category, and
uses the same beginner tool labels.
Its bottom status bar uses color-coded status and active-asset summary pills.
Hovering a dock header or row explains how to move panels, activate a layer,
or restore a history snapshot.
Navigation and keyboard-bound shell actions show their shortcut directly in
the button as well as in its hover tooltip.
The shell's primary actions, compact tool shelf, and chat prompt expose the
same hover-card pattern with a plain-language description and shortcut when
one exists.

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
6. The **Home** control opens the VOXTART project surface; `Esc` cancels
   transient editor work but never closes or replaces the VOXTART UI.


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
initial minimal path; hovering a control shows its label and available shortcut.
On Windows, the host window is branded **VoxTart** and uses the voxel-cake
icon at `assets/voxtart.ico`.
The launcher migrates both new and existing isolated preference directories
to disable Blender's native splash, then displays the custom **VoxTart
preview build** splash with the transparent-background icon before
opening the voxel editor.
The splash card uses rounded visual hierarchy, a clear product eyebrow, a
focused making tagline, and an explicit editor-preparation status strip.
The handoff invalidates the splash draw state and forces a redraw so the editor
does not inherit a stale startup frame.
The packaged `VoxTart.exe` launcher embeds the same transparent icon and
reveals a maximized window with the native **VoxTart** title bar. Standard
minimize, maximize, and close controls remain available while Blender's
internal editor chrome stays hidden.
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

## Licensing and source

The VOXTART add-on, launcher, tools, and release probes are licensed under
**GNU GPL-3.0-or-later**, matching the GPL-compliant Blender distribution.
The full text is in `LICENSE` beside this add-on and in
`doc/license/GPL3-license.txt`.

Packaged releases include `VOXTART-NOTICES.txt`,
`SOURCE-CODE-OFFER.txt`, and the complete GPL text under `LICENSES/`.
The bundled Blender runtime retains its own `license/license.md`,
`copyright.txt`, third-party notices, and source baseline in `UPSTREAM.lock`.

## Authoring tools

### Asset model and palette

- `VoxelAsset` stores integer grid cells, a normalized RGBA palette, grid
  spacing, named sockets, and provenance.
- Each palette swatch stores a MagicaVoxel-style material preset: `DIFFUSE`,
  `METAL`, `GLASS`, `EMISSIVE`, or `CUSTOM`. Presets drive Principled Base Color,
- In the VOXTART sidebar, open **Color Picker**, choose a swatch, and click
  **Edit Material**. In the voxel editor, the palette panel also exposes a
  Krita-style hue ring and HSV triangle; click or drag the wheel to edit the
  active swatch directly. Glass uses blended transparency; emissive colors use
  the Principled emission inputs.
- Hovering the hue ring or HSV triangle explains which color property the
  drag changes.
- VOXTART viewport shading uses the palette material color directly with flat
  lighting, so voxel colors remain faithful to the Color Picker without studio
  light, cavity, or specular modulation. Render/export materials retain their
  configured Principled behavior.
- Material settings are serialized with the asset in `palette_materials`, so
  `.voxel.json`/Blender custom-property persistence and palette remapping retain
  the selected material per swatch.
- Stable JSON serialization supports scene custom properties and portable
  files.
- Palette tools accept HEX/TXT, GIMP GPL, JASC PAL, JSON, and Adobe ASE
  data. The advanced Palette Library can import one of the listed free
  palette sources by its explicit source URL.
- Palette operations add/remove colors, remap affected voxels to the nearest
  target color, select an editing swatch, and apply a scene palette.
- The editor exposes a **PINNED** swatch row: `Alt`-click or right-click a
  swatch to favorite it, then click the pinned swatch for a quick selection.
  Favorites persist as scene metadata and follow a color when its HSV value is
  edited.
- Swatch headers show the active color's display-space HEX value and material
  kind. Hovering either a regular or pinned swatch opens the same metadata card,
  including the color slot and the pin/unpin gesture.


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
- a visible **Colors** strip selects the active palette swatch without opening
  Blender's sidebar; the adjacent hue ring and HSV triangle let artists tune
  that swatch by click-dragging;
- **More** keeps Hints and Advanced tools one click away without crowding the top bar;
- `G` toggles the bounded grid directly, while **Grid** in **More** exposes the
  same control;
- **Grid** can be turned on to show a bounded 3D box around the active asset,
  with one line per voxel interval at the asset's grid spacing;
- **Hints** reveals a small, optional block and layer summary;
- the default overlay stays lightweight: the asset, block count, beginner
  tools, palette, and a short guidance card;
- `Esc` cancels transient editor work but never closes or replaces the VOXTART
  UI or exposes a raw Blender workspace;
- the compact VOXTART toolbar keeps project tools one click away; Blender's
  native toolbar, sidebar, gizmos, infinite floor grid, and status bar stay hidden;
- **Grid** draws the bounded voxel-spaced box instead of exposing Blender's
  infinite floor grid;
- the full VOXTART vertical toolbar groups advanced brush and selection tools,
  highlights the active tool, and exposes a tooltip on hover;
- the VOXTART product panels expose palette, layer, history, and scene controls
  without revealing Blender's native panels.
- The **History** drawer names committed transitions (`Added 1 block`,
  `Colored 2 blocks`, `Moved asset`) instead of generic step numbers. Clicking
  a row restores that snapshot and reports the restored action.
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
  rebuild the mesh immediately on the first click and once more on release;
- a low-contrast, viewport-only wire seam traces occupied voxel edges so
  adjacent cells remain visually legible without changing the rendered asset;
- the editor spaces grid lines at the asset voxel size and highlights the
  current mouseover target with an always-visible translucent cell and outline;
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
  cancels an unfinished stroke; it never exits the VOXTART editor UI.

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
viewport and render visibility remain separate scene object properties.
Open the editor directly from the VOXTART launcher, from the shell's **Edit
blocks** action, or from the global `Alt+Shift+Q` shortcut. Use **Hints** to
show the optional block/layer summary, while **Advanced tools** expands the
VOXTART tool strip. Blender's native sidebar and menus remain unavailable.

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

The panel uses the current editor `voxtart_grid_size` as the final block size
and requires it to match the active asset `grid_size`. **Pixels / Block**
controls source density separately: the default `2` groups each 2x2 source
pixel neighborhood into one editable voxel using its dominant opaque color.
This keeps pixel-art edges hard while cutting the editable cell count by about
4x; set it to `1` when exact one-pixel topology is required. Mesh UV projection
continues to use the original texture pixels, so reducing edit density does
not blur or resample the material.

### Calibrated multi-view reconstruction

The deterministic reconstruction core accepts one to six indexed
orthographic views through `voxtart.reconstruct`:

- `OrthographicView` records axis, direction, top-left origin, pixel rows, and
  bounded integer depth;
- `DepthObservation` adds an optional per-pixel front/back interval and
  confidence layer. `LAYERED` consumes the interval; `SHELL` retains the
  configured front layers;
- `PartMask` attaches semantic pixel labels without turning labels into
  hidden geometry. Occupied voxel part assignments are serialized in
  provenance together with source identities and observation digests;
- transparent pixels become empty-ray constraints and indexed pixels become
  occupied-ray constraints;
- `SURFACE` selects the nearest viable cell, `LAYERED` retains each observed
  interval, `SHELL` retains a bounded front shell, while `VOLUME` retains
  every viable cell;
- visible-only material projection colors only the frontmost rendered cells;
  `EVIDENCE` keeps the deterministic ray-vote colors for all selected cells;
- cells that remain possible but unselected are returned as an explicit
  unresolved set rather than being silently guessed;
- deterministic render-back metrics report missing, extra, conflicting, and
  color-mismatched pixels;
- source view names, count, depth mode, part assignments, and source
  identities are preserved in asset provenance.

The converter is intentionally indexed and orthographic; arbitrary perspective
inference is not part of this path. The Blender adapter exposes the same
calibration contract without changing the authoritative voxel core.

### Image and palette intake

`voxtart.image_intake` converts calibrated RGBA rows into indexed source views
without filtering by default:

- source dimensions and nearest-neighbor pixel identity are retained;
- fully transparent pixels can become background through an alpha threshold;
- `NONE`, `ALPHA`, `CORNER`, and `EXPLICIT` background policies are explicit;
- palettes are extracted by deterministic frequency ordering with
  lexicographic tie breaks across one frame or a shared multi-view set, or
  accepted from the caller;
- bounded palettes map overflow colors to the nearest stable entry;
- each indexed image records dimensions, background settings, palette size, and
  an SHA-256 digest for reconstruction provenance;
- `IndexedImage.coarsen(pixels_per_voxel=2)` is the explicit middle path for
  pixel art: each block uses the dominant indexed color in its source
  neighborhood, reducing cell count without anti-aliasing the artwork;
- `IndexedImage.to_view` applies axis, facing, origin, integer depth, and
  priority calibration before calling `voxtart.reconstruct`.

The deterministic `voxtart.sprite_pipeline` composes intake, calibrated
reconstruction, candidate review, optional missing-only symmetry, a mesh or
pre-voxelized guide prior, and a skeleton payload. The
`voxtart.sprite_sequence` API reconstructs independent frames and reports
added, removed, and recolored voxel cells relative to the base frame.
`voxtart.guide_prior.MeshGuide` and `voxelize_mesh_guide` provide a portable
closed-mesh-to-voxel guide path before evidence reconciliation; the guide
cannot override explicit free evidence.
Typed `VoxelAsset.part_assignments` persist per-voxel semantic labels in the
asset payload and provenance compatibility field. Recognized labels produce
deterministic semantic role anchors (`HEAD`, `TORSO`, `ARM_L`, and related
roles) in `SkeletonGraph`; unlabeled assets remain explicitly geometric.

`voxtart.reconstruct_ui` loads Blender-supported image files, creates a new
non-destructive voxel asset, and stores source/calibration/report metadata,
evidence, acceptance, depth-observation digest, part-mask digest, and typed
part assignments. The single-view and multi-view panels accept optional
strict, size-bounded JSON files for per-pixel front/back depths and semantic
part labels. Selecting `Review candidates` generates the deterministic policy
set, selects the stable winner, persists every candidate report and the
selected policy on the Blender object, and displays the review in the panel.
They draw unresolved candidate cells as a purple wire overlay and report
unresolved, conflicting, missing, extra, and color-mismatched pixels.

### Spriteform sequence workflow

The **Spriteform Sequence** panel accepts a schema-1 JSON manifest and
reconstructs a deterministic base pose plus per-frame voxel deltas:

- image and optional depth/part-mask paths resolve relative to the manifest;
- defaults and frame overrides cover axis, facing direction, integer origin,
  depth, pose ID, and camera ID;
- all frames must share dimensions and use one extracted palette;
- optional depth observations and semantic part masks are preserved in each
  frame view and its provenance; they require `Pixels / Block = 1`;
- `SURFACE`, `VOLUME`, `LAYERED`, and `SHELL` reconstruction modes are
  available, with a 256-frame and bounded-cell guard;
- the created asset stores the manifest path, compact sequence report, and
  JSON pose deltas (`added`, `removed`, and `recolored` cells).

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

### Advanced authoring handoff

The Blender-native release gate exercises the advanced authoring operators
end-to-end: Lasso Blob conversion, active-layer Bloformer edits, generated
Pixel Tiler conversion and stamping, whole-voxel shading, non-destructive
dithering cleanup, and animated spritesheet export. The spritesheet exporter
restores frame, camera, render, visibility, and shading state after each
temporary render, including runtimes that expose an empty in-memory render
result in background mode.


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
The project shell exposes **Snap active to target** for the common two-object
handoff; the full Zoo Gallery/sidebar remains the authoring surface for named
joints, socket metadata, and compatibility feedback.

### Voxel Character Editor

The **Voxel Character Editor** assembles a reusable character recipe from
Blob Gallery parts instead of baking one monolithic mesh.

The project shell keeps the compact rig, animation, and export controls in
Character mode; the full **Voxel Character Editor** sidebar owns recipe
assignment, slot replacement, and character cleanup.

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

The VOXTART surface includes a centered bottom **Ask VOXTART** prompt bar.
It sends prompts to the configured local OpenAI-compatible model endpoint with
the same bounded MCP tools used by external clients, so a model can inspect,
create, and edit VOXTART assets. **Image** attaches up to four local PNG, JPEG,
WebP, or GIF references as standard multimodal data URLs; an image-only send
uses the reference as the starting point for a voxel request.
The editor and desktop shell's **Image**, **Prompt**, and **Send** controls
expose compact hover cards; **Prompt** and **Send** explain the `Enter` path
when the field is focused.

The bridge is opt-in and localhost-only. The MCP panel owns start/stop and the
bearer-token handoff; scene mutations stay disabled until the user enables
**Allow LLM scene edits**. The release probe verifies that boundary, a bounded
tool round-trip, and temporary server cleanup.

## Verification and coverage

The repository includes standard-library tests for deterministic core behavior,
Blender-native behavior, a headless end-to-end editor matrix, and Windows GUI
probes:

```text
python tests/python/voxtart_core_test.py
blender --background --factory-startup --python tests/python/voxtart_blender_test.py
blender --background --factory-startup --python tests/python/voxtart_e2e_blender.py
python tests/python/voxtart_coverage.py --all-missing --minimum-percent 100.0
blender --background --python tests/python/voxtart_blender_coverage.py
python -m compileall -q scripts/addons_core/voxtart tests/python tools
```

On Windows, the complete runner also exercises the compiled launcher:

```text
python tests/python/voxtart_e2e.py
tools\run_voxtart_e2e.cmd
```

`voxtart_e2e.py` runs the core suite, the Blender-native suite, the seven-test
headless editor matrix, a save/reopen persistence probe, a background launcher
probe, and a visible `VoxTart.exe` GUI probe. The persistence probe saves a
`.blend` in one Blender process and recovers the interrupted stroke and
clipboard in a second process. The GUI probe invokes the real operator in a
`VIEW_3D`, verifies both draw handlers, and validates a rendered screenshot
containing the editor overlay and brush preview. Use `--skip-launcher` only
when the compiled Windows launcher is unavailable.

The current local regression baseline is 112 deterministic core tests, 33
Blender-native tests, and 7 additional end-to-end Blender tests. The E2E
performance check covers a 32³ asset, mesh rebuild, connected/flat/lasso/circle
selection, and overlapping layer composition.
The latest local coverage probes report 7,955/7,955 core lines (100.00%) and
6,452/14,335 Blender-facing lines (45.01%). The latest headless performance
probe reported 32³ rebuild in 584.15 ms and selection in 65.75 ms on this
workstation.
The CI core-coverage step enforces the 100.0% deterministic-line threshold;
coverage regressions fail the Linux core job instead of becoming upload-only
evidence.
The workflow pins the official Blender 5.2.2 Linux and Windows archive
SHA-256 digests before extracting either runtime, and grants the jobs only
read access to repository contents.
All reusable GitHub Actions are pinned to immutable commit revisions, and
checkout steps disable credential persistence after fetching source.
The three direct suite entrypoints also enforce their collected-test contracts:
112 core tests, 33 Blender-native tests, and 7 headless editor tests. A suite
that silently loses coverage now fails its own process before CI can publish a
false-positive pass.

`voxtart_coverage.py` and `voxtart_blender_coverage.py` use `sys.settrace`;
neither requires pytest or a third-party coverage package. The GitHub Actions
workflow checksum-verifies its pinned Blender runtime downloads, runs the
deterministic, Blender-native, and headless E2E suites on Linux, then builds
the Windows runtime and launcher, runs the complete Windows P0 matrix with an
isolated `VOXTART_CONFIG`, and uploads the GUI screenshot
and marker as workflow artifacts.
Artifact uploads use `if-no-files-found: error`, each CI job verifies its
expected artifact files before upload, and schema checks validate coverage,
performance, regression-baseline, GUI, and success-marker payloads so a
passing test job cannot silently discard invalid evidence.
