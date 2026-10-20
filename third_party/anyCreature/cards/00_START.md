# 00 START (read first after unzip — the only always-on rules)

## The pipeline in one line

```
order → ONE required question → silhouette brief → LOW: design FREE + two gates → MID (2 rounds) → HIGH (1 round) → SHIP (scripted delivery + closing dialogue)
```

## Iron laws

1. **Form beats obedience.** Every rule below exists to protect form quality; if following a rule would make the creature tamer, the rule loses. Edits whose only purpose is to make a number match a declaration are forbidden.
2. **You never grade your own thumbnails — and you never trust a reader either.** Every gate is read by a context-free agent who knows nothing about the order, and every read carries the verification traps of card 01 §4 (binding questions, a canary, shuffled labels): assume every reader is lazy until its answers prove otherwise. Its verified verbatim answer is the verdict.
   **A reader is the most expensive thing in the run** — a subagent bills for EXISTING: with no images and a one-line prompt it still costs the great majority of what a full read costs, so almost all of a read's price is fixed overhead. Three rules follow, and all three are in the cards: batch the images (a five-image read costs barely more than a one-image read), never spend a read the machine could answer (`roundcheck --preflight` refuses those), and never let one round test one idea.
3. **Same symptom failed twice = no third tweak.** Restart that view/element from a DIFFERENT concept. If the restart also fails, ship your best version and write one honest DEVLOG line about what didn't land. **This one is counted, not trusted** — `harness/roundcheck.py` records each gate verdict and reads `iou_vs_prev` to tell a real restart from a nudge, and refuses the third tweak. Law 2 exists because you cannot grade your own thumbnails; this law had the same problem and now has the same answer. A run can spend rounds 6–15 moving the same head and ears before restarting at r16 — eight rounds, and by then every round costs three times what round 6 did, because the context they ride on keeps growing.
   **A round tests THREE ideas, not one** (card 01 §4b). A repair builds three versions that differ in KIND and shows all three to one reader — three builds cost four seconds more than one, while three separate reads cost three times a read. Three sizes of the same head is not three ideas; it is one idea with a wobble, and this law counts it as one.
4. **The signature part gets real geometry at LOW.** A giant whose identity is its fists gets fingers, knuckles and a planted pose in the silhouette round — not a placeholder sphere. Budget follows 6:3:1: the 6-level element gets 6-level geometry.
5. **Materials are named per PART** (`skin_torso`, `fur_leg`, `eye`, `tusk`…) — the area rulers identify parts by material name.
6. **Engine floors block builds** — mechanical checks refuse broken geometry and fake attacks (`BLOCK:` lines say exactly what; `warn:` lines are measures for YOUR judgment; `info:` lines narrate what the compiler actually did). Fix and rebuild; a refused build costs no round.
7. **The customer is a non-designer, and gets exactly ONE question — which you MUST ask.** Never skip it, however complete the order looks: choosing a direction is what makes the creature theirs, and a creature that simply arrived is one they will not show anyone. Card 01 asks it (three directions if the order was thin, "which feature is the star?" if it was specific) and that is the last word they hear until the closing dialogue in card 04. In between you decide everything and ask nothing.
   **ONE means one message, and it is the FIRST thing you send.** Not one question bundled with two others, not "a few quick things before I start". Between the order and that message you run `setup.sh` and nothing else; the very next words the person reads are card 01's question, alone. A second question in that message is a violation even if the question is a good one.
   **Never ask about:** the delivery format, file type, triangle count, folder layout, whether to run a check, whether your expansion of the order is acceptable — **nor what it will cost, how many tokens or rounds it will take, how long you should spend, nor anything about publishing, sharing, uploading, or where the file should end up.** Those are decided by this harness, not by the customer. Cost and scope are your job: a non-designer has no way to price a round and asking makes them do yours. Publishing is decided in card 04, after the creature exists — nobody can consent to sharing a thing they have not seen, so asking early buys a worthless answer at the price of the one question that mattered. If you truly cannot proceed, choose the option you can defend, state the choice in one line at delivery, and keep moving.
8. **Nothing is ever uploaded without the user's explicit yes, this session — and that yes is collected in card 04, never before.** No background uploads, no test uploads, ask for every creature. This law says you may not upload unasked; it does not license an early ask, and reading it as one breaks law 7. The share question comes after the creature exists, after they have seen it, in card 04's closing dialogue. Asking it at the start is a violation of both laws at once — it spends the single question they had on a decision they cannot make yet.
9. **One creature, one agent. The reader is the ONLY subagent.** You build the whole thing yourself — brief, skeleton, parts, anims, repairs — in one session, and you spawn subagents for exactly one purpose: the context-free reads of law 2. Handing the modelling itself to a chain of subagents feels tidy and is measurably worse on every axis: on the same creature to the same green build, splitting it four ways (skeleton → parts → anims → verify) cost more tokens, more wall clock and more turns, because each fresh agent re-derived what the previous one already knew — one of them rebuilt a simulator of the compiler's own ring maths from scratch. The reader splits cheaply for the opposite reason: it is one turn, and it *must not* inherit anything. Cost of a split = how much understanding the next agent has to rebuild. For the reader that is zero, which is the point of it; for the modelling that is everything, which is the price of it.
10. **Write state down every round; never rely on your own memory of it.** A boss session outlives its own context — the transcript gets compacted and the early rounds are gone. That is a record problem, not a cost problem, and splitting the work does not fix it (see law 9). What fixes it: after every round, the archived `spec.json`, the metrics, and the reader's verbatim verdict are on disk under `out/rN/` and in `log.md` before you move on. Anything not written there did not happen, and after a compaction you resume by reading those files, not by trusting what you think you remember.

## Every check is tagged twice — read the map before arguing with one

`harness/gates.json` lists every check in the system; `python3 harness/gates.py`
prints it. Two tags on each:

- **block / advise.** Block is for things that are *broken* — geometry that is
  not geometry, a rig that does not deform, a clip that does not exist, an
  identity the blind read could not name. Advise is for things that are
  *debatable*: 6:3:1, focal contrast, saturated area. A number that says the eye
  will ping-pong is worth reading and is not worth a rebuild. **Advice never
  stops a build.** If you find yourself rebuilding because of an advisory, you
  have misread the tag.
- **allocate / verify.** Allocate = decidable *before* the build, so budget it
  at generation time and it cannot come out wrong. Verify = only knowable after
  the thing exists — it ran, it rendered, someone looked at it. `gates.py` prints
  the ones that are blocking *and* allocatable at the end: each of those can
  refuse a build over something that should simply have been budgeted, and every
  such refusal is a round that did not need to happen.

Where the visual load sits: **LOW carries it, MID carries one colour read, HIGH
carries none.** By HIGH every visual decision is already made, so a gate there
cannot improve the creature — it can only cost a round. HIGH blocks rig and clip
only.

## The thing that actually costs money: TURNS

Not tokens, not tools, not compute — **round-trips.** Every command you issue
re-reads the entire conversation up to that point, and the conversation only
grows, so an identical command costs several times more at round 20 than at
early in a run. Split into separate commands, compiles, silhouette renders and
measures are most of a run's round trips. None of those tools is slow: a compile is 1.4 seconds, a silhouette view half a second,
the whole measure phase under ten. The tools were never the bill.

So the harness gives you ONE command per phase, and you use it:

| instead of | run |
|---|---|
| build + silhouettes + measures + preflight | `python3 harness/round.py out rN --spec …` |
| a build + render per whitelisted part | `python3 harness/partreads.py out/qa --spec …` |

Same rule everywhere else: if two commands have no decision between them, they
are one command. Loop in the shell, not in the conversation.

**LOW no longer needs a browser at all.** Silhouettes come from the geometry:
`harness/outline.py` projects the triangles under the same camera the renderer
used and fills them, so the outline is computed rather than photographed and
then measured. Verified against the rendered masks on all four views at IoU
0.980–0.989 — the remainder is the renderer's antialiased edge. A round went
from 9.0s to 1.4s and a three-variant round from 11.0s to 4.4s. **1.3.2 finished
the job: nothing in this harness launches a browser at all.** Part shares are the
same z-buffer, the colour numbers are the baked albedo the engine already wrote,
and even `hero.png` is projected rather than rendered. The offline viewer is HTML
the customer opens themselves.

## Environment self-check (run verbatim)

```bash
bash setup.sh     # deps + red/green ruler calibration; must print "calibrate OK"
```

## Closing ledger (one DEVLOG line at delivery)

`gates: ID pass@r? PUNCH pass@r? | restarts: none-or-what | unresolved: none-or-what`
