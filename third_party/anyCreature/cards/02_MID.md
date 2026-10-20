# 02 MID (budget 2 rounds | build: all parts, flat colour, ZERO new bones)

Output: every part — ears, horns, claws, eyes, fins, shell spikes, paws — riding
bones LOW already placed, or anchored to volume surfaces. No material detail, no
animation. MID's question is: **do the key parts READ as what they are?**

## Author the WHOLE part set in one pass. Build once. Fix everything at once.

This is the single most expensive habit in the pipeline, so it gets the top of
the card. Do not add parts one at a time and rebuild after each. Write every
part, run one build, read the whole BLOCK list — the engine reports **all**
failures at once, it does not stop at the first — fix them all in one edit, and
build again.

The arithmetic on a 28-part body:

- the entire spec is a **few percent** of one build's output. The spec would have
  to be written **dozens of times over** to account for the run. Writing the spec
  is not the cost.
- MID takes ~130 turns for 40 elements — **every part and volume touched 3.2
  times on average.** That is the cost.

A turn re-reads the whole conversation, which only grows. Touching 40 elements
three times each is 120 round-trips over material that could have been written
once and corrected once. Two passes is the target: **author everything, then
repair everything.**

Mirrored structure is not authored twice, either. `"mirror": ["LArm"]` generates
the right side; hand-writing `RArm` alongside `LArm` doubles the work and the
chance of the two drifting apart. Use `mirror` for anything symmetric, and
`joints_R` when the pose (not the shape) differs.

## MID SPENDS NO READER AT ALL

Both reads that used to live here are gone, and neither was replaced by a
cheaper read — they were replaced by arithmetic that runs at build time.

**The isolated part read is deleted.** It failed every time it was tried — a part
cut out of the body comes back as "a blob", "a stone", "a fist" — while the
whole-body read of the same body had already named the parts. A part cut away
from the body has lost the only context that told the reader what it was, so
reading it there measures the crop, not the part.

**The colour read is deleted too.** It asked three things, and a machine answers
all three off the palette in microseconds:

| the question | who answers it now |
|---|---|
| what colour is this creature | the palette. You wrote it. |
| which part draws the eye | `value_order` — every material sorted by OKLab lightness, brightest first, printed on every build. Whatever is at the top owns the eye. |
| which parts can you make out | `contrast_adjacent` — every part measured against the material of the thing it SITS ON. Under 0.10 OKLab they read as one mass. |

Eyeballing "value readability" costs hero renders and then a MID reader, to be
told that a shin blade is the same colour as the leg under it. Both answers sit
in the palette the whole time — a declared focal at L 0.975 tied for brightest
with a claw at L 0.973 is exactly what those renders are being used to guess.

Both checks are ADVICE with numbers, not blocks: camouflage and deliberately
subtle detail are real choices. What is not acceptable is paying a subagent to
learn them.

**`part_exists` still BLOCKS**, and it costs nobody — it is a machine check on
the spec. If the brief named a chain and the spec has no such chain, the order
was not filled, and `harness/brief.py` says so at r1.

**What a reader is still for**, anywhere in this pipeline: *what does this look
like*, *which of these is punchier*, and *is this form legible*. Those are
judgments about resemblance and impact, and no amount of coordinate data
performs them. Colour distance, value order, silhouette aspect, feature
thickness and animation momentum are all arithmetic — none of them gets a
reader again.

`harness/partreads.py` stays for a deliberate manual look at one part. No card
calls it.

## Edit vocabulary (what a MID repair is allowed to be)

- **swap the local shape**: replace the primitive-ish mass with structured
  geometry — brow + muzzle + jaw instead of a head-sphere; knuckles + fingers
  instead of a fist-ball. **No naked sphere / cube / cone may remain visible
  on a whitelisted part.**
- **fold and carve**: concave sections, `sharp` profile breaks on the profile
  rows, a lower `smooth_angle` on that volume (spec default 50°), plates and
  spikes that cut the outline. **`faceted` on a VOLUME is a BLOCK**
  (`faceted_body`): it shatters an 800-triangle torso into 800 shards, and AO
  bakes that mess into COLOR_0 where no relighting can reach it. Parts — fins,
  spikes, claws, crystal, armour — may still be faceted freely.
- **bend the pose**: elbows/knees bend at MID — place the bend at its JOINT
  (`joints` positions form the bind pose). Straight arms read as dead arms.
  Mirrored pairs may stagger via `joints_R` (grow symmetric, pose asymmetric) —
  **but only by a few centimetres.** The twin's mesh is grown on the LEFT bone
  path and then translated onto the right joints, so a large offset shears the
  volume rather than posing it (a right thigh at 40% of the left's depth, width
  untouched — invisible from the left, invisible in the build log). Past ~10% of
  the limb's length it starts squashing and `mirror_distortion` warns; past ~35%
  it BLOCKs. For a genuinely different pose, take that limb OUT of `mirror` and
  author it as its own chain.

## Hands and feet — use the vocabulary, never improvise from spheres

Anything with hands gets `"type":"hand"` — palm, four fingers and an opposable
thumb, scaled from one `size` number; `curl` for relaxed/gripping, `"fist":
true` for the folded fist with the thumb wrapped across. Field truth: every
attempt to improvise a hand from spheres and sticks shipped a mitten. Feet:
`paw` with `"toes": 3..5` so they stop reading as bread loaves. Both take
`mirrored: true`, and the blind-read applies — a hand that reads as "a blob"
fails like any whitelisted part.

## Joins — decide HOW every part meets the body, before placing it

A tusk that starts in mid-air, a beak hovering off the face, a trunk that reads
as a bolted-on object: all one disease — the part was placed, but its RELATION
to the body was never decided. For every hosted part, pick the join first:

- **insert** — the part SINKS into the mass (tusks into the jaw, horns into the
  skull). Declare `"join":"insert"`; the engine BLOCKs unless the base ring is
  ≥60% buried. Aim the `offset` back INTO the host volume, not at its surface.
- **extrude** — the part GROWS out of the skin (a trunk from the face, a tail
  spike from the tail). Declare `"join":"extrude"`; the base centre must sit
  inside a body. Match the base radius to the local host radius so the skin
  flows into the part instead of stepping.
- **snap** — the part LIES ON the surface (plates, scutes, brows). That is what
  `anchor` + conform already do; declare `"join":"snap"` and the engine insists
  the anchor exists.
- **place** — deliberately detached (a floating rune, an orbiting shard). Rare;
  say why in the spec `_notes`.

Undeclared parts still get measured — a `part_seat` warn means a root may show.
And the join is also a COLOUR decision: a part that is FLESH of its host (trunk,
tail, brow) continues the host's material colour at its base; a hard colour
break at the junction reads as equipment, not anatomy. Check the junction in the
hero render, not just the part in isolation.

## Do (layout)

1. **Signature presence**: silhouette share is a BUDGET check — the signature
   part hits its declared share (judge measures by material name); nothing else
   needs a share test.
2. **6:3:1 lands**: geometry budget follows the hierarchy; the 6-part gets
   6-level structure.
3. **Busy-vs-calm**: on one part, one edge detailed, the other long and clean.
4. **Declared connections touch**: masses that must read as one body declare
   `"touch": [["torso","tail"]]` — the engine BLOCKS if they don't overlap.
5. **Read the warns**: `part_overlap` lines flag parts sitting inside other
   parts (the self-intersecting-fist class). A warn is a measure, not a law —
   look at the render and decide.

## Don't (blood of previous runs)

- **Mirrored inward-tilted parts cross at the midline**: length × sin(tilt) vs
  the left-right gap — do the arithmetic BEFORE placing horns/fins.
- **Parts must not cover focals**: a horn base once covered the eye. After
  placing anything big, re-check the focal view.
- **Ride the right bone**: jaw/tongue ride the skull, or they detach in motion.
- **Every part must TOUCH its host** — `part_attachment` BLOCKs a part whose
  nearest point still stands clear of the host surface. Tusks, trunks and plates
  used to float with the build reporting all green, and only a human eye caught
  them. The first third of a root is meant to be buried; that is what hides the
  seam. A part that cannot name what it touches is not attached.
- Eyes/ears anchor to the HEAD volume surface: `"anchor":
  {"chain":"head","t":0.40,"around":62}` — `around` in degrees from the top of
  the section (0=spine, 90=side, 180=belly). Wolf-class eyes sit at 55–70.
- On an anchored plate the host surface wins by default: the plate is snapped
  onto the surface normal and the direction you wrote survives as a reported
  difference. `"conform": false` when that written direction was deliberate.

## Stage-end gate

- Machine: build green + MID-stage claims (`part_exists` / `part_visible` /
  `part_signature` / `share_hierarchy` / `focal_contrast`).
- Whitelist blind-reads all passed.
- Human look #2: focal distribution right? part-to-body seams clean?
- Pass = part layout locks.
