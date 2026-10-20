# 04 SHIP (scripted delivery + closing dialogue + optional community publish)

## Red lines (absolute)

- **Never upload anything without the user's explicit YES, this session.**
  Not to test, not in the background, not because a card says the flow exists.
- **Ask about sharing for every creature**, even if the user shared the last one.
- **Once they say yes, RUN the upload. Always try it first.** Do not decide in
  advance that this environment cannot reach the network — cloud sandboxes,
  Cowork sessions and CI runners frequently can. Run `publish.mjs`, then answer
  from what it printed. ❌ "in a sandbox you can only upload manually" — never
  say this. ✅ "try the upload, fall back to the link if it fails."
- The web page `gobkit.com/community/upload` is the BACKUP path only, and it is
  offered ONLY after a real attempt came back `blocked`.

## 1. Gate stamp (real results only)

**The engine already wrote most of it. Do not retype it from memory.**

Every build drops `<out>.checks.json` beside the GLB — every engine check by
name, whether it passed, whether it merely warned, and the verbatim BLOCK and
measure lines. It is written by the code that ran the checks, on refused builds
as well as green ones. Start from that file:

```bash
cp out/creature.checks.json delivery/gate.json     # the engine's half
```

Then add ONLY the checks the engine cannot know about — the blind-read verdicts,
which live in `out/rN/round.json` where `roundcheck --record` put them as each
round happened:

```json
{ "name": "gate1_recognised", "passed": true,  "round": "r1", "reader": "a giant bat" }
{ "name": "gate2_punchier",   "passed": true,  "round": "r2", "reader": "incumbent held" }
{ "name": "mid_colour_read",  "passed": false, "round": "r5", "reader": "dried leaves and straw" }
```

Never invent a line, and never reconstruct one you could have copied. If a check
didn't run, it isn't in the file. This is the most expensive moment in the run to
be remembering things — the conversation is at its longest and a token here costs
several times what it cost at round 3 — and iron law 10 already says memory is
not the record. Both halves are on disk. Copy them.

## 1b. Conformance is automatic — you do not run anything

The engine contract-checks every GLB the moment it writes it, `ship.py`/`deliver.py`
re-check the stamped file, and the publisher refuses to upload a file that fails.
Names and signatures with markup are cleaned at stamping rather than blocking the
delivery. A contract failure means a harness bug: report it with the spec, do not
work around it.

## 2. Ask ONCE, then run ONE command

Ask in the USER'S language, in one breath (ownership first — they name it and
sign it before any talk of sharing):

> Give your monster a name? (Enter = <working name>)
> Your signature? Asked once — remembered from now on. (Enter = anonymous)
> Share it to the Gobkit community? Uploading releases it under CC0, permanently —
> anyone can download, use and remix it. [yes / no]

The signature question is skipped when `~/.anyCreature.json` already holds one.

```bash
# they said no — package only
python3 harness/ship.py out/<name>.glb --name "<their name>" --author "<signature>" --gate delivery/gate.json

# they said yes — package AND publish, one command
python3 harness/ship.py out/<name>.glb --name "<their name>" --author "<signature>" --gate delivery/gate.json --publish
```

`ship.py` stamps the name and signature INTO the GLB (they travel with the file
forever), packs the viewer, `hero.png` and the backup upload pack, and — only
with `--publish` — uploads and prints the result. Never pass `--publish` without
an explicit yes in this session.

## 3. Read the result back, verbatim

`ship.py` prints one JSON line last. Answer from it, in the user's language:

| status | tell them (one line) |
|---|---|
| `published` | ✅ Published: "<title>" by <creator> → <share_url> |
| `pending_review` | ⚠ Uploaded — the server is verifying it; it will appear shortly. (no further explanation) |
| `blocked` | The upload pack is ready in `delivery/upload/` — open https://gobkit.com/community/upload and drag `creature.glb` in. No account, no key needed. (a next step, NOT an error — nothing is broken, nothing was lost) |
| `error` | The server refused — read its message out verbatim, then offer the same backup page. |

**Two different handles come back, and they are NOT interchangeable.** The claim
link puts the model under their Gobkit account (open it, sign in with Google);
the takedown code removes it, needs no sign-in, and CANNOT be re-issued. Read
`manage_kind` from the JSON — it says which of them you actually got:

| `manage_kind` | what to say |
|---|---|
| `both` | give BOTH lines below, in this order. This is the normal case. |
| `url` | the claim link line only |
| `token` | the takedown code line only |

- 🔑 Claim it: `<manage_url>` — open it and sign in with Google to put this
  monster under your account. The link stops working once it is used. Saved in
  `delivery/claim_link.txt`.
- 🧯 Takedown code (shown ONCE, cannot be re-issued): `<manage_token>` — the
  only way to remove the model later, no sign-in needed. Do not post it. Saved in
  `delivery/manage_token.txt`.

Never let one of them stand in for the other, and never drop the code because a
link was also given — that is exactly the bug this table exists to prevent.
Give them in the SAME message as the share link, never below the fold.
`claim_link_file` and `manage_token_file` are the paths they were written to;
quote those paths, not guessed ones.

**Never hand over a claim link without checking it resolves.** The publisher
already did: `claim_link_live` is `true` (it answered), `null` (could not ask —
say nothing, the link is probably fine) or `false` (it answered 404). On `false`
the claim link is dead; give `claim_fallback_url` instead — the standing claim
page, where the same takedown code is pasted after signing in. `ship.py` prints
whichever of the two applies, so quoting its output verbatim is always correct.

**If they said no, or the upload failed, leave them one line:** *"say the word
and I'll upload it — one command, nothing is rebuilt."* The command is
`python3 harness/ship.py delivery/<name>.glb --upload-only`. A creature that was
finished and never reached the wall is the most expensive kind of miss.

**Reading a `blocked` result.** The submit endpoint always answers in JSON, so an
HTTP 403 is a filtered network, never a key problem. `ship.py` already classifies
these; do not re-diagnose them as authentication failures, and never tell the
user to fetch or change a key.

## 4. Closing ledger

**ONE line. Not a narrative.** Format in card 00:

```
gates: ID pass@r? PUNCH pass@r? | restarts: none-or-what | unresolved: none-or-what
```

Plus the delivery checklist: glb, viewer, heroes, spec JSON, gate.json, DEVLOG.

**The round-by-round record is already on disk** — iron law 10 requires the
archived spec, the metrics and the reader's verbatim verdict under `out/rN/` and
in `log.md` *as each round happens*. The closing DEVLOG does not repeat any of
it. It says how the gates landed and what did not get solved, and stops.

This is the most expensive moment in the entire run to write prose: the
conversation is at its longest, so every token here costs several times what the
same token cost at round 3. It is easy to write **thousands of characters** of
session narrative here — tables, verbatim reader quotes, per-round analysis —
all of it duplicating `log.md`, all of it at maximum context price. One line is
the rule.
