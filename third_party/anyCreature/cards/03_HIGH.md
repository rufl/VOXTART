# 03 HIGH (budget 1 round | build: colour + three animations)

## Colour — you choose freely; five norms bound the choice

Pick the palette yourself, from the creature's story. The norms:

1. **One high-saturation MAIN colour, spent on the signature part.** Saturation
   is a spotlight; if everything is saturated, nothing is.
2. **One secondary colour** for the big masses — quieter than the main.
3. **The ACCENT stays under 5%** of the surface (eye glow, claw tips, markings)
   — the memory spark. One accent temperature, not two. The 5% caps the ACCENT
   alone; it is not a cap on how much of the creature carries saturated colour —
   norm 4 sets that, and its floor sits well above 5%.
4. **Saturated area: 10%–34% — computed, not judged by eye.** The
   `saturation_area` claim counts the share of the view carrying HSV saturation
   ≥ 0.50, read on the UNLIT baked vertex colour, so brightness, AO and lighting
   cannot skew it. Below the floor the creature reads as a grey mass; above the
   ceiling saturation stops working as a spotlight. The band rules HOW MUCH,
   never WHERE — which surfaces carry the loud colour is your call. It agrees
   with norm 1: one main colour on the signature plus its supporting bands lands
   mid-band (the wolf example measures 26.0%). Out of band, raise or drop
   saturation on a mass that deserves the attention; do not tint everything.
5. **Brightness floor — do not crush to black.** The judge measures the beauty
   render's median luminance; a "dark" creature reads by VALUE STEPS between
   its masses, not by making everything dark. If the render medians below the
   floor, lift the mid masses, keep the darks only where a step needs them.

The engine bakes **per-vertex AO at compile** (crevices, pits, undersides
darken automatically — that's the "solid" look). The floor is computed AFTER
AO; never fight AO by flattening your colours, and never disable it to pass
(`"ao": false` exists only for debug builds).

Vertex colours come from two places. Per volume, `colors.arcs` bands the
section over that material's base colour (0°=spine, 180°=belly) — bands must
follow the form, spine band darkening the top plane, belly band lifting the
underside. Everything after that is the **L1–L8 stack**, and it is a FORMULA:
it runs on every build, it takes no decisions, and there is nothing here for
you to judge. That is the whole reason HIGH has no visual gate.

What it does, in order: smooths the flesh colour across part boundaries so
junctions cannot show a seam (a colour field that is a function of POSITION
gives two coincident points the same value, by construction); lays the pattern
if the spec named one; multiplies a top-to-bottom three-colour ramp over
everything; brightens **and** saturates the upper region while leaving the
lower region exactly untouched; bleeds hardware colour into the flesh around
it; shades hardware and flesh separately; and finally softens the shipped
NORMAL on flesh toward the bone field, which is the one layer the viewer's own
lighting reacts to.

**The only field you normally write is `shading.pattern.color`, and you decided
it at MID.** Everything else is settled — see `cards/SYNTAX.md` for the full
block; the settled values themselves are `DEFAULTS` in `engine/core/shade.js`.
Two things worth knowing: brighter-and-more-saturated is impossible with any
blend mode (every mode that brightens moves toward white, and white has chroma
zero), which is why that layer works in OKLab; and pushing chroma walks back to
the sRGB boundary by bisection rather than clamping, because clamping per
channel turns the hue instead of capping the saturation.

The build prints one `info: shade class ...` line naming which pieces are
flesh, which are hardware and which are features. **Read it.** A trunk is a
`curve`, so it defaults to hardware and will not take the flesh treatment until
the spec says `"shade": "flesh"`. `"shading": {"stack": false}` falls back to
the 1.2.0 ramp-and-grain for a spec that was tuned against it.
UV atlas is opt-in (`"keep_uv": true`) for downstream texture bakes; by default
the shipped file carries colour in vertices and stays lean.

## Materials — what the format can and cannot carry

- **No textures at all.** Colour is per-vertex; the whole palette ships in the
  file. Do not plan for a texture, an emissive map or a normal map — there are
  none, and the contract refuses images.
- **Every material carries its own hue** in `baseColorFactor`; the engine does
  this for you by splitting the baked colour (hue into the material, shading
  into COLOR_0). Never hand-write a pure-white base colour with the colour in
  vertices only: a viewer that ignores vertex colours then shows a white
  creature. That happened to a published creature on a public gallery.
- **Keep `metal` low — 0.2 at most, and only where it earns it.** Metal without
  an environment map renders NEAR-BLACK, and most galleries, engines and preview
  tools default to no environment. A brass creature should be a bright warm
  colour with low metalness, not a metallic material hoping for reflections.
  The checker warns above 0.3.
- Roughness is free: use it to separate surfaces (wet, chalky, polished).
- **No anisotropy, no brushed metal, no hair sheen.** `KHR_materials_anisotropy`
  stretches the specular highlight ALONG THE TANGENT, and these meshes carry no
  tangents and (by default) no UVs — so the extension has no direction to work
  with and every renderer invents its own. The result is a flat white smear, and
  turning the strength DOWN does not help: what is missing is *which way*, not
  *how much*. The checker BLOCKS it (`anisotropy_without_direction`). If you
  want a directional look, get it from GEOMETRY — grooves, plates, aligned
  spikes, a darker vertex band along the axis — or unwrap first with
  `"keep_uv": true` and accept that the palette is still vertex colour.

## Animation — three, always: `idle`, `move`, `attack`

**Write all three, then build ONCE.** Not one clip, build, look, next clip. The
three share a skeleton, a stance and a weight story — the same hips that bob in
`move` are the hips that wind back in `attack` — so authoring them together is
easier as well as cheaper, and it is the same rule card 02 applies to parts. The
engine checks every clip in one pass (`anim_integrity`, `attack_reach`,
`limb_clearance`) and reports all of their failures at once, so a build per clip
buys nothing but round-trips at the most expensive point in the run.

Tracks are cheap to write and expensive to iterate: three clips of a big
creature come to a few dozen tracks and a couple of thousand characters. The
characters were never the problem. Three builds instead of one were.

- The bar for idle/move: skinned, actually moving, no clipping, no explosions.
- **`attack` must COMMIT FORWARD — but it does not have to lunge.** The engine
  CPU-skins the clip and BLOCKS (`attack_reach`) only when nothing commits at the
  space in front of the body. Two ways to satisfy it, either is enough:
  **REACH** — something ends up ≥15% of the body span past the bind-pose front;
  **SWING** — some part travels forward ≥45% of its own length. So a creature
  planted on the spot, winding a foreleg, a tail or a weapon BACK and sweeping it
  FORWARD, passes. A ±20° twitch does not, and neither does a sideways sweep that
  never crosses toward the front. Wind up BACK first, then strike FORWARD.
- **Gait for many legs.** Two legs: opposite phase (`mirror_phase: 0.5`).
  Four legs: DIAGONAL pairs — front-left moves with back-right; offset the
  back pair's keys by half a cycle relative to the front pair, then
  `mirror_phase: 0.5` staggers left/right. Six or eight (spiders): alternating
  tripods/tetrapods — two leg groups, one group's keys offset half a cycle;
  adjacent legs must never move in sync or the creature skates instead of
  walking.
- Channels stack: several tracks bending one segment = keep total bend modest,
  or `anim_integrity` blocks (folds / >3× edge stretch). Reduce bend or add a
  joint; don't fight the check.

```jsonc
"animations": {
  "move":   { "duration":0.95, "loop":true, "mirror_phase":0.5,
    "tracks": { "LFrontRoot": {"rx":[[0,-26],[0.5,28],[1,-26]]} } },
  "attack": { "duration":0.7, "loop":false, "tracks": {
    "Chest": {"rx":[[0,0],[0.25,-14],[0.5,22],[1,0]]},          // wind up, strike
    "Root":  {"tz":[[0,0],[0.25,-0.1],[0.5,0.9],[1,0]]} } }     // …and LUNGE
}
```

## Stage-end gate

- Machine: full claims re-run (styles, `rig_skinned`, `anim_named` incl.
  attack, motion amplitude, tri budget) — save the claims output, 04 ships it
  as the gate stamp.
- Human look #3 (final): value readability + play all three animations once.

Then read `cards/04_SHIP.md` — delivery and closing are scripted.
