# Handover — submission sanitising (harness side → server side)

**To:** whoever builds the gobkit.com/community sanitiser
**From:** the anyCreature harness side
**Re:** your discussion sheet of 2026-08-17 (file disinfection for submitted GLBs)

Sanitising itself is yours: it has to sit where files arrive, and it has to
survive people who edit the harness. This document is what we owe you for it —
measurements you asked for, the shape of what we send you, and a component you
can run instead of writing the cheap gate yourself.

---

## 1. Your direction is right, with one correction

Whitelist-rebuild over blacklist-scan: agreed, and the strongest argument for
it is not in your sheet. It is **duplicate JSON keys**: `{"generator":"clean",
… ,"generator":"<payload>"}`. A scanner that reads the object sees the first
value; every loader (and `JSON.parse`) keeps the last. Scanning and loading
disagree by construction — no blacklist can close that, and rebuilding closes
it without knowing it existed. Worth putting in your §5 as the load-bearing
example.

Three more carriers missing from your §4:

- **ZIP polyglot.** Your "tail after the declared length" row, made concrete:
  a file that is a valid GLB from the front and a valid ZIP from the back (ZIP
  is parsed from the end). Your gallery becomes the distribution point for the
  archive. Exact-length rewrite plus tail truncation kills it.
- **Accessor bombs.** Not smuggling: `count: 2^31`, `NaN` in `min`/`max`, a
  `bufferView` index that does not exist, an animation channel targeting a
  missing node. These crash the *downloader's* loader. A rebuild that
  validates ranges removes the class.
- **Markup in names.** Node and material names are shown by viewers and
  galleries. `<img onerror=…>` as a mesh name is an XSS payload aimed at
  whoever renders your listing, not at the model.

Everything else in your §4 and §5 we agree with as written.

---

## 2. §6.1 — the measurements

Pure JS (`pngjs`), single-threaded, no native libraries — the only path
available inside a Worker. Node's V8 versus workerd's V8: same order of
magnitude, so treat these as the shape of the answer, not a promise about your
account's limits.

| Input | Full sanitise: parse → decode every texture → re-encode → rebuild |
|---|---|
| 10 MB GLB, 8 × 1024² pixel-art textures (your realistic case) | **CPU 1.0–1.5 s** |
| 8 × 1024² pure-noise textures, 23.5 MB (worst case that cannot even be submitted — over your 10 MB ceiling) | **CPU 2.0–2.5 s** |
| anyCreature file (no textures at all, vertex colours) | **0.2 ms** |
| Cheap structural gate only (`glbcheck`, any file) | **~1.4 ms** |

Against Cloudflare's limits — please re-verify against current docs, this is
from our side's knowledge and platform limits move: paid Workers allow ~30 s
CPU per request (configurable), the free tier ~10 ms. So:

- **Paid tier: full rebuild fits inside the upload request** (1–2.5 s ≪ 30 s).
  Memory is not a concern either (10 MB file + 8 decoded RGBA buffers ≈ 32 MB
  against a 128 MB ceiling).
- **Free tier: only the cheap gate fits.** The rebuild has to go to a queue.

Even on the paid tier we would put the rebuild in a queue, for reasons that
have nothing to do with CPU: retries come free, texture budgets can grow later
without re-architecting, and it lines up exactly with your "visible on upload,
download locked" work order — **the lock lifts when the rebuild finishes.**

---

## 3. Recommended shape

```
upload  →  cheap gate (ms)  →  visible immediately, download LOCKED
                │ fails: reject now, quote the error code to the submitter
                ↓ passes
           queue → whitelist rebuild → download UNLOCKED
                                     │ fails: reject, notify, do not keep a zombie
```

**Do not treat harness-side output as a defence.** You said it yourself in
§6.7: the harness is open source, so anyone can edit or bypass it. What
harness-side work *is* good for is making the honest majority trivially cheap
to process — which is what section 4 is about.

---

## 4. What we send you, and the fast path it enables

`docs/OUTPUT_CONTRACT.md` is the full contract. The short version: an
anyCreature 1.3.0 file is **two chunks, zero trailing bytes, one embedded
buffer, no URIs of any kind, no images at all** (colour lives in vertex
colours with AO baked in — so there is no EXIF/XMP surface to strip), no
compression extensions, semantic material names, convention bone names,
lowercase `idle`/`move`/`attack`, metres, +Z forward, 150–800 KB.

Two consequences for you:

- **The expensive half of sanitising does not apply to these files.** Texture
  decode/re-encode is the entire CPU cost in your budget, and there are no
  textures. Rebuilding one is pure JSON+buffer work: sub-millisecond.
- **There is a stronger option than rebuilding for these files.** Every
  anyCreature GLB embeds its own authored spec (`asset.extras.source_spec`),
  which is plain JSON data and recompiles byte-identically. The ultimate
  sanitise is to **recompile from the spec**: the output then contains only
  bytes our zero-dependency compiler wrote, and nothing from the uploaded file
  survives except the design intent. The engine is dependency-free JS and can
  in principle be bundled into a Worker. We are not proposing you do this now
   — it is the natural phase two once the general rebuild is running.

## 5. `harness/glbcheck.mjs` — take it, it is yours

Zero dependencies, no I/O, one export, never throws:

```js
import { checkGLB } from './glbcheck.mjs';
const { ok, errors, warnings, info } = checkGLB(uint8array);
```

It rejects: extra chunks, trailing bytes, external URIs, mandatory
compression, duplicate keys, path leaks, markup/control characters in names,
accessor bombs, malformed containers. It reports `info.anyCreature` so you can
route conforming submissions to the fast path, plus `info.animations`,
`info.skinned`, `info.images`, `info.harness_version`. Measured at **~1.4 ms**;
verified against five hostile fixtures (ZIP tail, extra chunk, external URI,
path leak, duplicate keys) and clean files.

**Read its own header comment before you rely on it:** it is a validator, not
a sanitiser. It repairs nothing and must not be your only line. Use it as the
cheap gate in front of the queue.

---

## 6. The rest of your questions

**§6.2 extensions.** Allow-list: `KHR_texture_transform` (the black dragon
uses it), `KHR_materials_unlit`, `KHR_materials_emissive_strength`. Outside
the list but *not* in `extensionsRequired` → drop the extension, keep the
model, note it on the listing. In `extensionsRequired` and unsupported (Draco,
Meshopt, quantisation) → **reject the submission**: you cannot rebuild what
you cannot decode, and `llms.txt` already forbids them.

**§6.2b `KHR_materials_anisotropy` — drop it unless the mesh can carry it.**
Anisotropy stretches the specular highlight ALONG THE TANGENT. A primitive with
neither `TANGENT` nor `TEXCOORD_0` gives the renderer no direction, so each one
invents its own and the model is a flat white smear in some viewers and correct
in none — and lowering `anisotropyStrength` does not help, because the missing
information is *which way*, not *how much* — dropping `anisotropyStrength` from
0.9 to 0.15 changes nothing. On a submission whose primitives lack both, DROP
the extension and keep the model — the base colour is unaffected and the creature renders
correctly without it. anyCreature 1.3.0 refuses to emit this case at all
(`anisotropy_without_direction`), so any file carrying it was hand-edited or
came from another tool.

**§6.3 texture quality.** Lossless PNG out, always. These are pixel-art and
flat-colour textures; lossy artefacts are visible at a glance and the
submitters will notice. JPEG in → PNG out (already lossy once, do not stack a
second generation). If the source has ≤256 distinct colours, write an indexed
PNG — usually *smaller* than the original. If the clean file exceeds 10 MB,
reject with a reason rather than silently degrading.

**§6.4 metadata to write back.** Use the fields the submission API already
reads, do not invent a second vocabulary: `asset.generator = "gobkit"`,
`asset.copyright = <creator>`, and under `asset.extras`: `title`, `license:
"CC0-1.0"`, plus `harness`, `harness_version`, `gate` and `source_spec` when
present. Keep `source_spec` (re-parse and re-serialise it, cap it at 64 KB) —
it is what makes wall creatures remixable, which is the point of CC0.

**§6.5 failure handling.** Malformed or unknown structure → reject. Timeout →
queue retry twice, then reject. **Never "keep but never unlock"** — a
permanently locked row is storage you pay for and a submitter who is never
told anything. Reject with a machine-readable code so the agent can quote it:
`external_uri`, `unsupported_extension`, `broken_image`, `oversize_after_clean`,
`parse_error`, `timeout`, plus `glbcheck`'s codes (they are listed in the
output contract, use the same words on both sides).

**§6.6 the existing gallery.** Yes, re-clean it. It is a small set, the slug
stays, the bytes change — purge the CDN cache after. People who already
downloaded have the old bytes; under CC0 that is harmless and not worth a
notification.

**§6.7 where to run it.** Covered in section 3: server-side, both stages;
harness-side output is a cheapness optimisation, never a boundary.

**§7 headers.** Agreed, do it now, and add two more of the same near-zero
cost: `Content-Disposition: attachment` and
`Cross-Origin-Resource-Policy: same-origin`.

---

## 7. What we are NOT doing

We are not building the sanitiser, and we will not pretend the harness output
is safe by construction just because it is clean today: someone can hand-edit
a file and still stamp it `anyCreature`. `info.anyCreature` from `glbcheck`
means "claims to be, and has the right shape" — it is a routing hint for the
fast path, never an authorisation.
