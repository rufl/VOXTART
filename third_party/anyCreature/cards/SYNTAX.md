# Spec JSON — the whole language on one page

```jsonc
{
 "height": 1.85,        // real-world metres, ground to crown — the engine verifies ±15%
 "palette": { "skin_torso": {"color":"#8a8a80","rough":0.95}, ... },  // one material PER PART
 "sections": { "flute": [[1,0],[0.6,0.4],...] },   // named 2D outlines, CONCAVE allowed (bark, crescents)

 "smooth_angle": 50,    // default: faces meeting at a vertex average when their normals sit within
                        // this many degrees; the vertex splits only at a REAL crease. Override per
                        // volume and per part. "faceted":true == smooth_angle 0.
 "shading": {           // THE L1-L8 STACK. Every field below is optional and every
                        // default is the settled value — you normally write NOTHING
                        // here except `pattern.color`, which is a MID decision.
   "pattern": {"color":"#ffffff","sharpness":0.45,"amount":1.0,"scale":0.06},
                        // L2. Omit `color` and there is no pattern. Flesh only.
   "ramp":   {"bottom":"#001370","mid":"#cfcfcf","top":"#fffcf0","pm":0.31,"wm":0.08},
                        // L3. Top-to-bottom, multiplied over everything but features.
   "boost":  {"y0":0.00,"y1":0.40,"gamma":0.30,"dL":0.03,"dC":1.50},
                        // L4. Brighter AND more saturated up top, EXACTLY nothing below
                        // y0 (identity, not "almost"). Chroma walks back to the sRGB
                        // edge instead of clamping — clamping turns the hue.
   "bleed":  {"radius":0.025,"sharpness":0.35,"amount":0.45},   // L5 hardware into flesh
   "hardsh": {"amount":0.54,"gamma":0.70},                      // L6 shadow on hardware
   "bodysh": {"lights":4,"rot":4,"elev":17,"amount":0.20,"gamma":1.95},  // L7 on flesh
   "normals":{"flesh":0.90},   // L8 — the ONLY layer that leaves COLOR_0 and goes into
                        // the file's NORMAL, so the user's lighting reacts to it.
   "stack": true },     // false = the 1.2.0 ramp-and-grain instead (gradient/noise below)
                        // legacy, only read when stack:false:
                        // "gradient":{"top":0.30,"bottom":-0.88}, "noise":{"size":0.018,"amount":0.26}
                        // noise.size = FRACTION OF THE MODEL DIAGONAL, not metres.
 "build": "rigid",      // robots/constructs/golems/vehicles ONLY — lifts the faceted_body BLOCK

 "joints": {
   "Hips": [0, 0.9, 0],                            // absolute [x,y,z], y up, z forward
   "Chest": {"from":"Hips","fwd":0.1,"up":0.5},    // relational: fwd=+z up=+y side=+x
   "TailTip": {"from":"Tail2","dir":[0,-1,-0.4],"len":0.3},
   "LToe": {"from":"LKnee","fwd":0.1,"ground":0.03}  // ground = absolute Y (feet)
 },
 "joints_R": { "RElbow": {"from":"Chest","side":-0.3,"up":0.25} },
   // optional: reposition auto-mirrored R* joints — pairs GROW symmetric but the
   // bind POSE staggers (raised right arm); the mirrored skin follows its joints.
   // A FEW CENTIMETRES ONLY. The twin's mesh is grown on the LEFT bone path and
   // then dragged onto the right joints by translation, so a large offset shears
   // the volume instead of posing it: past ~10% of the limb's length it starts
   // squashing, past ~35% the right thigh comes out 40% of the left's depth with
   // its width untouched. mirror_distortion BLOCKs that. For a genuinely
   // different pose, take the limb OUT of "mirror" and author it as its own chain.
 "chains": { "torso":["Hips","Spine","Chest"], "LArm":[...], ... },
 "attach": { "LArm":"Chest", "head":"Neck" },      // every non-root chain → its host joint
 "mirror": ["LArm","LLeg"],                        // auto right-side twins (joints, meshes, anims)
 "touch": [["torso","tail"]],                      // declared connections MUST overlap or BLOCK

 "volumes": [{
   "chain":"torso", "material":"skin_torso", "sides":14, "frame":"up",
   "profile":[[0,0.3,0.35], [0.6,0.42,0.5,{"bias":-0.1,"sharp":true}], [1,0.2,0.22]],
     // rows [t, half-width, half-height, opts]; exp 2.5-4=boxy slab; bias<0 belly-full;
     // sharp = hard SILHOUETTE break, and it only bites when the radius STEPS
     //   across it (>=15%). The flag between two rings of the same radius is a
     //   no-op. It changes nothing.
   "smooth_angle": 30,     // per-volume override, and the SHADING lever. The wall
     //   angle is 360/sides; smooth_angle welds every edge under it, so 9 sides
     //   (40 deg) or 16 sides (22 deg) at the default 50 is welded perfectly
     //   smooth. A volume that can break NOWHERE — no facets, no radius step —
     //   is a bean, and the engine BLOCKs it (soft_mass). Lowering it hardens the
     //   shading and leaves the outline untouched.
   "soft": true,           // OPT-OUT for soft_mass: this mass really is meant to be
     //   a smooth organic lump (a slug, a bladder, a droplet). Deliberate, not default.
   "shade": "flesh",                               // OPTIONAL. flesh | hard | fx — which shading
     // class this piece belongs to. The default is right almost always: volumes, hands and paws
     // are flesh; spikes, curves, membranes and fins are hard; eyes are fx (no layer, no shadow).
     // Write it when the default is wrong for THIS creature: a trunk is a `curve` that is flesh;
     // an armour plate grown as a volume is a volume that is hard. The build prints which pieces
     // landed in which class ("info: shade class ..."), so check that line rather than guessing.
     // "faceted":true on a VOLUME is a BLOCK (faceted_body) — an 800-tri torso becomes 800 shards
     // and AO bakes the mess into COLOR_0. Break a body with "sharp" rows or a lower smooth_angle.
   "caps":["dome","dome"], "ring_step":0.05,
   "colors": { "arcs":[{"from":0,"to":52,"color":"#57513f"}] }      // 0°=spine 180°=belly
     // arcs ONLY — colors.gradient / colors.noise are ignored (info: line); see "shading" above
 }],

 "parts": [
  { "type":"curve", "host":"Head", "material":"tusk", "mirrored":true,   // CURVED horn/tusk/trunk
    "join":"insert",                 // how it meets the body: insert (base buried, verified) /
                                     // extrude (grows out of the skin) / snap (rides an anchor) /
                                     // place (deliberately free) — undeclared floating roots warn
    "offset":[0.06,0,0.1], "dir":[0.3,-0.6,0.7], "sides":8,
    "segments":[ {"len":0.1,"r":0.035,"ahead":20}, {"len":0.1,"r":0.028,"rise":35},
                 {"len":0.08,"r":0.015,"rise":30,"taper":true} ] },
    // steering is per-segment and ADDS UP down the chain. rise/fall/ahead/behind
    // pull the heading that many degrees toward that world axis; coil swings it in
    // the carried plane (rings, spirals); taper pinches the far end.
    // Four mild segments can finish somewhere you did not predict — the compiler
    // reports the accumulated total (info: ... accumulated to 123°). Read that line.

  { "type":"membrane", "name":"wing", "material":"wing_skin", "mirrored":true,  // skin between rib chains
    "cusp":0.25, "along":8, "across":3,
    "ribs":[ {"chain":"LArm"}, {"chain":"LFing2"}, {"chain":"LFing3"}, {"chain":"LTrail"} ] },
    // along = samples down each rib · across = columns between neighbouring ribs
    // cusp = how deeply the trailing edge scoops · per-rib "shorten":0.2 pulls one rib in
    // Ribs run leading→trailing and the LAST one has to come back to the body.
    // Leave it out at the far end and the silhouette never encloses — it reads as
    // spread fingers, not one sheet. The compiler measures the root gap and warns.

  { "type":"fin", "host":"Skull", "material":"plate", "thickness":0.02, "mirrored":true,
    "anchor":{"chain":"head","t":0.4,"around":60},   // around: 0=spine 90=side 180=belly
    // ⚠ that mapping is the BODY chain's frame. `around` is read in the host
    // SECTION's frame, so on a chain running vertically (a leg) the same numbers
    // point elsewhere: 0=inner, 90=FRONT, 180=OUTER, 270=back. Aiming a plate at
    // the outside of a thigh with 90 lands it on the front. The compiler prints
    // the world direction each anchored part actually faces — read that line,
    // do not reason from this comment.
    "udir":[0,0,1], "vdir":[0,1,0], "points":[[0,0],[0.1,0.02],[0.05,0.15]],
    "faceted":true },   // PARTS may face freely (or set "smooth_angle") — only VOLUMES are blocked
    // by default the host surface wins: an anchored part is snapped flat onto the
    // surface normal, and the direction you wrote survives only as a reported
    // difference. Set "conform":false when that written direction was the point.

  { "type":"eye", "host":"Brow", "material":"eye", "size":0.028,
    "anchor":{"chain":"head","t":0.4,"around":62} },  // both eyes from one entry; side of head ≈55-70

  { "type":"hand", "host":"LWrist", "material":"skin_hand", "mirrored":true,  // palm + 4 fingers + OPPOSABLE thumb
    "size":0.17,                          // palm length — the whole hand scales from it
    "dir":[0.1,-0.9,0.35], "up":[0,0,1],  // dir = where the knuckles point; up = back of the hand
    "curl":0.35, "spread":0.5,            // relaxed curl / finger fan; "fist":true = folded, thumb wrapped
    "join":"extrude" },

  { "type":"paw", "toes":3, "host":"LToe", "material":"skin_fist", "size":[0.3,0.25,0.2], "mirrored":true }
 ],

 "animations": {
   "move":   { "duration":0.95, "loop":true, "mirror_phase":0.5,
     "tracks": { "LFrontRoot": {"rx":[[0,-26],[0.5,28],[1,-26]]} } },   // rx..tz keys [fraction,deg|m]
   "attack": { "duration":0.7, "loop":false, "tracks": { ... } }
     // attack must COMMIT FORWARD — either route passes (attack_reach):
     //   REACH: something ends ≥15% of the body span past the bind front, or
     //   SWING: some part travels forward ≥45% of ITS OWN length.
     // You do NOT have to lunge. Planted on the spot, winding a limb, tail or
     // weapon BACK and sweeping it FORWARD counts. A sideways sweep does not —
     // the strike has to commit at the space in front of the body.
 },
 "style": "heavy",     // ONLY if 1:1 segment rhythm is the design (relaxes the 50:50 gate)
 "ao": false,          // debug only — vertex AO bakes automatically otherwise
 "embed_spec": false,  // opt OUT of the embedded birth certificate (default: the GLB carries its own spec)
 "keep_uv": true,      // opt-in UV atlas (TEXCOORD_0) for downstream texture bakes
 "qa_isolate": true    // MID part-isolation builds only: skips whole-body checks
}
```
`BLOCK:` = build refused (fix, costs no round) · `warn:` = a measure, you judge ·
`info:` = the compiler telling you what actually happened — always read them.
Shipped GLBs auto-merge primitives per material and export public bone names
(`LArm1Sh` convention) — internal spec names never leak.

---

## `function` — what each chain is FOR  (1.3.2)

```jsonc
"function": {
  "body":   "axis",         // the load-bearing spine of the creature
  "LArm":   "locomotion",   // what moves it: legs, wings, fins, wheels
  "RArm":   "locomotion",
  "LLeg":   "effector",     // the end that reaches out and contacts the world
  "case":   "ornament"      // carried, not load-bearing, does no work
}
```

Keys are chain names. A chain listed in `mirror` covers its twin automatically.
A part bolted onto a chain inherits that chain's function, so `"neck": "effector"`
covers the blade mounted on the neck; naming the part directly also works.

Five words, all manufacturing vocabulary. Nothing here says how hard a thing hits or
what it is worth in a fight — that is the receiving system's business.

**A declaration is not a label; it is a claim the engine checks:**

| check | what the declaration has to pay for |
|---|---|
| `effector_leads` | at the frame the attack reaches furthest, the effector is the frontmost mesh |
| `attack_windup` | the effector winds back 5% of its travel, or swings 15% out to the side |
| `gait_footfall` | a locomotion chain that touches ground lands in FRONT of where it lifts |
| `self_clip` | nothing that starts far apart ends up inside something else |
| `clip_closes` | a non-looping clip returns to its first frame |
| `ground_clip` | nothing goes below the plane the creature stands on |

`harness/autofix.mjs` fixes the arithmetic ones before a round is spent — the ground
offset, the loop closure, and the compensation of joints that must hold their own aim
while the body turns for some other purpose. It never touches the decisions: which way
a leg swings, what the creature attacks with, how far it lunges.

## `joint_range` — how far each joint turns  (1.3.2)

```jsonc
"joint_range": {
  "LKnee":  { "rx": [-130, 10] },              // a knee is a hinge, one way
  "LSh":    { "rx": [-50, 90], "ry": [-60, 60], "rz": [-70, 70] },
  "Beak":   { "rx": [-10, 40] },               // opens, does not close past shut
  "BallTop":{ "rx": [-35, 35], "ry": "free" }  // hangs on a chain, spins freely
}
```

Degrees, per axis, in the joint's **own frame** — the same frame and the same axis
names (`rx`/`ry`/`rz`) the animation tracks use, so a limit and a track compare
directly.

- **An axis you leave out is LOCKED at 0**, not free. A table with holes in it is a
  table that checks nothing, and one axis per joint is the common case anyway.
  `"free"` opts an axis out on purpose — a rotor, a wheel, a weight on a chain.
- **Declare the L side only.** The R twin is generated: `ry` and `rz` negate, which
  swaps the ends of the interval, so `[-10, 90]` becomes `[-90, 10]`. Writing both
  sides by hand is how mirrored limbs get broken. Writing `R…` explicitly overrides,
  for a body that really is asymmetric.
- **Write what the ANIMAL can do**, not what your clip happens to use. A limit is a
  fact about the body; a track is one use of it. If they are the same numbers, the
  declaration is not saying anything.

**What the declaration has to pay for:**

| failure | what it means | the fix |
|---|---|---|
| undeclared | a clip turns a joint with no entry | write the entry |
| overrun | a track leaves its own declared interval | fix the track, or the number — one of them, not both |
| unreachable | the body collides before the declared limit | tighten the number to the angle the engine reports |

`unreachable` is the one worth understanding. You know a gorilla's shoulder swings 60°
back; you do not know that THIS gorilla has a wrecking ball hanging off a crane on its
back, so its arm stops at 53°. The engine sweeps every declared bound against the actual
geometry and reports the last angle that clears. Authored knowledge, checked arithmetic —
neither one alone is enough.

## `name` on every part — the identity channel  (1.3.2)

```jsonc
"parts": [
  { "name": "nose_leaf",  "type": "fin",   "host": "Muzzle", "material": "paw" },
  { "name": "foot_claws", "type": "paw",   "host": "LToe",   "material": "paw", "mirrored": true },
  { "name": "left_tusk",  "type": "curve", "host": "Muzzle", "material": "tusk" }
]
```

Required, and unique within the creature. It may not reuse a chain name, because
volumes are identified by their chain.

**Why it is not optional, and why the material cannot do the job.** A material is a
CLASS — "this is horn", "this is brass pipe". Many parts share one on purpose, and the
GLB merges primitives one per material so a body with forty plates does not ship forty
draw calls. That makes a material name structurally unable to answer *which part is
this*: above, `paw` names both a foot pad and a nose-leaf, and anything that groups by
material adds the two together. Naming is the fix, and it costs nothing — you already
know what you are building at the moment you write the part.

Name the THING, not its type and host. `spike@Skull` is a description of where you put
something, and two spikes on one skull share it; `left_horn` and `right_horn` are names.

The engine writes each name into the file as `asset.extras.part_spans`, with the exact
vertex range that part owns — so a checker, the Arena importer or `graft.py` can point
at one piece of geometry without a single extra material.
