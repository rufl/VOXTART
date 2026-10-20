# 01 LOW — design free, then two gates (the big-form stage)

## 1. ONE question — REQUIRED. Exactly one, then silence until delivery.

You MUST ask it. Not "if the order is unclear", not "if you need to know" —
always, even when the order is long and specific and you could proceed without
it. The question is not there to collect information; it is there to let the
person put their hand on the creature before it exists. A creature they chose a
direction for is theirs; one that simply arrived is yours, and they will not
show it to anyone.

So: exactly one question, immediately, before any building. Which of the two it
is depends on what the order already committed to — you decide which branch you
are in, you never ask which branch.

**Send it alone.** The message that carries this question carries nothing else:
no budget check, no "roughly how long do you want me to spend", no "shall I
publish it when it's done", no list of options to confirm. Those are law 7's
forbidden list and they are the common way this goes wrong — the person answers
three questions, only one of which shaped their creature, and the two that did
nothing are the ones they remember. If you have already sent something else,
you have spent the question; do not send a second one to make up for it. Take
the most defensible reading of the order, build, and say what you chose at
delivery.

**Thin order** ("a dragon", "a big monster") — offer three directions, one line
each, and let them point:

> Three ways I could take this — pick one, or say "you choose":
> 1. <a direction driven by MASS — what is oversized and what is starved>
> 2. <a direction driven by a WEAPONISED part — what it fights with>
> 3. <a direction driven by ANATOMY BROKEN ON PURPOSE — what is where it should not be>

The three must diverge on those three different axes, never be three colours of
the same idea. No answer, or "you choose" → take the first and start. Never ask
a second time, never ask them to confirm the expansion afterwards.

**Specific order** (it already names species, colour, parts, weapons) — the risk
is not vagueness, it is that everything they listed competes to be the star. Ask
which one wins:

> You mentioned <A>, <B> and <C> — which should be the thing people notice first?

That answer becomes the signature part: it takes the geometry budget, the
highest-saturation colour, and the identity view. No answer → pick the most
unusual of the ones they named (not the most expected).

**Everything else you decide yourself**, silently — size in metres, temperament,
proportions, palette, animations. `harness/claims.json` is the one claims sheet
(every creature is judged at boss standard): fill its `<signature part>` /
`<primary mass>` placeholders from your brief, and lower `tri_budget` only if
the creature is meant to be cheap.

## 2. The brief — expand the order yourself, never ask

Orders arrive thin ("a dragon"). A thin order is not a small job, it is an
UNDECLARED job: every slot the brief leaves empty is a slot no gate can test,
and an untested slot drifts to whatever the word usually looks like — the same
black spiky dragon every time. So expand it yourself, in one pass, before you
build. **Do not show the expansion to the person and do not ask them to
approve it** (the required question in §1 is a different thing: it comes
first, before any expansion, and it is the only thing they are asked). They asked for a creature, not for homework; the gates are what
catch a wrong guess, not a confirmation step.

The slots are not a style; they are the INPUTS every downstream ruler reads.
Fill all nine, in one short block, then freeze it:

| Slot | What it must say | Who reads it later |
|---|---|---|
| identity | "reads as: X" — the noun a stranger should say | Gate 1 (3 of 4 views, identity view mandatory) |
| feel | one phrase: heavy / fast / sharp / floating | the 24px read |
| height | real-world metres | `size` gate (±15%, engine BLOCKs) |
| signature | ONE named part, and which view carries it | `part_exists`, `part_signature`, MID whitelist, HIGH main colour |
| mass hierarchy | which masses are primary / secondary / detail | `share_hierarchy` (6:3:1) |
| two focals | the dominant one and the runner-up | `focal_contrast` (≥2× apart) |
| stance | how the weight is planted; what is asymmetric | `balance`, and the bind pose IS the pose |
| attack | what strikes, and what lunges forward | `attack_reach` (½ body span or BLOCK) |
| value plan | dominant COLOUR, secondary, accent <5%, dark or light — **no material words, no roughness numbers; nothing downstream reads them** | `saturation_area`, brightness floor |

Two rules that decide whether the expansion is worth anything:

- **Discard your first three ideas.** The first things that arrive for any
  creature word are the tropes everyone has seen. Name them to yourself, throw
  them out, and take the fourth. A brief that could have been written without
  the order in front of you has failed.
- **Commit to ONE exaggeration and pay for it everywhere.** Pick a single
  extreme — a proportion, a weaponised part, a broken symmetry — and let it
  distort the hierarchy, the stance, the attack and the colour plan. Nine
  moderate slots make a shapeless creature; eight ordinary slots serving one
  violent decision make a creature people remember.

These two rules are the highest-value minute in the whole pipeline. Repair
rounds refine mass; they do not invent objects. Spend the thought here.

**Two slots must also name the CHAINS they mean**, like this:

```
| signature  | the pair of giant pincers — carried by the SIDE view  chains: LClaw, RClaw |
| two focals | dominant = the right pincer · runner-up = the boulder in its net  chains: RClaw, net |
```

You invent the names; nothing checks whether they are good ones. What is checked
is that the spec you build at r1 actually contains them, so that a part the
brief ordered cannot go unbuilt until a later stage notices.

**The brief is checked for PRESENCE, never for content.** `harness/brief.py`
runs automatically inside `round.py` at r1. Declare any height, any identity,
any palette — all of them pass. What it refuses is a BLANK, and only in the
slots a later stage actually reads, because a blank does not make the downstream
ruler lenient, it makes the ruler VANISH:

| blank slot | what silently stops working |
|---|---|
| identity | `identity.py` falls back to a placeholder noun, so Gate 1 prints a verdict about nothing |
| height | `checks.js` guards the size gate with `if (spec.height)` — no declaration, no check, no message |
| signature | the one customer question in §1 IS this slot, and law 7 forbids asking a second one |
| value plan | `value_order` has nothing to compare its sorted lightness table against — it can say what IS brightest, not what SHOULD be |
| feel | the 24px read costs a whole subagent and has nothing to be judged against |
| the one exaggeration | §4b builds two stagings of it; there is nothing to stage |

Mass hierarchy, two focals, stance and attack are NOTED when blank and never
blocked — they cost an advisory at most, and blocking them would buy nothing
while costing design freedom, which is the entire point of this stage.

The order always wins on anything it actually specified. Everything it left
open is yours to decide — decisively, not cautiously.

## 3. Design FREE

Build the creature with the engine (syntax: `cards/SYNTAX.md`) exactly as you see fit — your own judgment, your own process. Only the pit-map applies (engine-local facts, not design rules):

- Anatomically independent masses get their OWN volumes, overlapping their neighbours (mane, shoulder, chest slab, head). Radius wiggle inside one tube reads as soft sausage, not as blocks.
- **NO ROUNDED SAUSAGES — and the default IS a sausage.** A volume's wall angle is `360/sides`, and `smooth_angle` (default 50) welds every edge under it: 9 sides is 40°, 16 sides is 22°. Leave the default on and nothing on that mass can break — not the shading, not the outline — and it renders as a glossy bean. The engine BLOCKs it (`soft_mass`) and states the fix in numbers. The ruler is the share of volume edges that can crease, and the floor is 10%. **The lever is `smooth_angle` ON THE VOLUME**, set under its wall angle: it hardens the shading and leaves the silhouette Gate 1 validated untouched. Fewer `sides` works too. A `sharp` row is the SILHOUETTE lever and does nothing unless the radius steps across it. A mass genuinely meant to be a smooth lump declares `"soft": true`.
- Wings spread flat in one plane vanish from the side — give them sweep-back. Every view needs a designed silhouette; a straight-line view is a dead view.
- The bind pose IS the pose. Creatures never stand bolt upright in a T-pose; plant the weight.
- Curved things (tusks, trunks, horns) are `curve`; membranes (wings, frills, sails) are `membrane` — don't fake either with straight spikes or flat plates.
- **Parts meet the body by a JOIN, not by luck.** A tusk is `insert` (base buried in the jaw), a trunk is `extrude` (grows out of the face), a plate is `snap` (rides an anchor). Decide the relation, declare it, and the engine verifies the root is really seated — card 02 has the four verbs.
- **Bends belong to JOINTS.** An arm that must flex needs its elbow joint placed where the bend lives; radius wiggle inside a straight tube gives you a bent sausage, not an elbow. Place the skeleton for the pose you want.

**Before the first round, make the spec LEGAL:**

```bash
python3 harness/fit.py pole_a.json -o pole_a.json
```

A refused build is refused for two different reasons. Some are DESIGN — the
shape does not read, the poles are one idea twice. Some are ARITHMETIC — a root
ring is not buried in its host, two neighbouring bones came out the same length,
the build does not land on its declared height. The engine already computes the
exact correction for each of the arithmetic ones and prints it inside the BLOCK;
`fit.py` reads those numbers back, applies them, rebuilds and repeats.

Do this and a build round is spent on the creature. Skip it and the round is
spent on legality, which is the single largest block of wasted time in a run:
the arithmetic failures are COUPLED — burying a root ring moves a joint, which
changes two segment lengths, which trips the proportion band, which moves a
joint again — so they cannot be cleared in one hand-edit either.

`fit.py` never touches a design decision: not `balance` (which mass moves is a
choice), not `mesh_integrity`, `touch` or `part_seat` (they say a relationship
is wrong, not by how much), and no style threshold at all. A clean run does not
mean the creature is good; it means the next round is about the creature.

A whole round is ONE command:
```bash
#   r1 — TWO OPPOSITE POLES (§4b), and the brief is checked here:
python3 harness/round.py out r1 --spec pole_a.json pole_b.json --gate ID
#   a repair round:
python3 harness/round.py out rN --spec spec.json --prev r<N-1> --gate ID
```

It builds every spec, renders the four silhouettes, runs the mask measures,
runs `roundcheck --preflight`, and prints ONE report ending in either "spawn
the reader" or a BLOCK telling you not to.

**At r1 it also checks the brief** (§2) and, when you give it two specs, whether
those two are really two designs rather than one design built twice. It finds
`brief.md` beside the spec, in the out directory or in the working directory;
`--brief <path>` overrides. Both checks are free — no render, no reader, no
extra turn — and r1 is the last moment either is cheap to fix.

**Do not run those four tools separately.** A command is a TURN, and a turn
re-reads the entire conversation so far, which keeps growing — the same command
issued late in a run costs several times what it cost early. The tools are not
slow; the round-trips are the bill.

**`--prev` is not optional from round 2 on.** It is what computes
`iou_vs_prev`, and `iou_vs_prev` is the only mechanical evidence of whether you
changed the shape or nudged it — without it `roundcheck.py` cannot enforce iron
law 3, cannot refuse a wasted read, and says so.

The report measures; YOU judge. Flags worth eyes: `sq_fill` (silhouette volume in a 1:1 frame), `mirror_sym` (only the FRONT view may be symmetric; wing pairs stagger even there), `straight_max` (plank-limb detector), and **`thinnest_px48`** — the width of your thinnest feature on the 48px thumbnail the reader actually sees. Under 3 px it is not a thin feature, it is an invisible one, and no amount of repositioning will make the reader see it: thicken it or drop it. This number is available on the FIRST build; a run that discovers it late has spent every round in between moving something nobody could see.

## 3b. Where the reader budget goes — LOW, not later

Blind reads are the most expensive line in the whole run and the only one that
buys identity. Spend them **here**, on the silhouette, and be stingy afterwards.

**A reader's price is almost entirely the cost of the subagent EXISTING**, not
the pictures inside it. Everything about how this card spends readers follows
from that: put images together in one read rather than splitting them across
reads, never spend a read the machine could have answered (`--preflight`), and
never let one round test only one idea (§4b builds two opposite poles).

The reason is what a failure costs to undo. A silhouette that reads as a fox
when the order said dragon is repaired by moving big shapes — cheap, and every
later stage inherits the fix. The same misread caught at MID or HIGH means
throwing away the part work, the anims and the shading that were built on top of
a wrong form. Identity is decided by the big shapes, the big shapes are all
there is at LOW, so LOW is the only stage where a blind read can still change
the answer instead of just recording it.

Practical split: both gates here get full reads with all three traps of §4. The
part reads in card 02 are a spot-check, not a re-litigation of identity — if MID
is where you first discover the creature is unrecognisable, LOW was under-read,
not MID.

## 4. Reader verification — ASSUME EVERY READER IS LAZY

A reader that glances at one image and confabulates the rest poisons every
verdict downstream. Every blind read, at every stage (both gates here, the part
reads in card 02), carries three traps. **A tripped trap voids the whole read —
but the repair depends on WHICH trap, because they do not mean the same thing.**

1. **The bump count, and ONLY when it is unambiguous.** For every image the
   reader first says *"how many things stick out of the outline?"* A gross
   mismatch (reader says 0, the mask counts 5) voids the read. Looking at image
   1 cannot produce image 3's bump count: laziness stays mechanically hard.

   **The "wider or taller?" trap is RETIRED.** Readers cannot reliably make that
   call at 48px inside roughly 0.55–1.45, and voiding a read on it throws away a
   whole reader. The trap dates from when aspect could only be read off a
   picture; `outline.py` computes it exactly from the vertices before anyone is
   spawned, so asking a reader to confirm it buys a worse copy of a number the
   machine already has. If you want it as a sanity question, ask it and IGNORE
   the answer unless the computed W/H is outside 0.55–1.45.
   The thumbnails `outline.py` writes are letterboxed, not squashed, so the
   proportion the reader sees is the proportion the file records — check it. (It
   was not always so: a `resize((px,px))` made every thumbnail square, the trap
   was unanswerable by construction, and 21 of 52 reads in one three-creature
   batch were re-runs of a question nobody could have got right. If you ever see
   every reader answering "square-ish", suspect the thumbnails before the reader.)
2. **A canary in every batch.** Shuffle ONE image from `harness/canary/` into
   the set under a neutral name (`answers.json` holds the key). Canary answered
   wrong = the reader was not looking = discard everything it said.
3. **Shuffled neutral labels.** Images are IMG-A/B/C in random order (Gate 2:
   randomize which of previous/current is which) — repeated or swapped answers
   expose themselves.

Readers are never told which image matters, which is the canary, or why.

### Two speeds of repair, because a trip has two possible causes

The old rule was one speed: any tripped trap, re-run it **one image per
reader**. On a five-image batch that is five readers where one would do, and
Gate 1 is where the reader budget goes; everything after it should be stingy.
The rule assumed every trip means the reader was lazy. It does not.

- **Canary answered wrong → isolate immediately, one image per reader.** There
  is no second explanation for this one. The canary is a picture with a known
  answer; getting it wrong means the reader was not looking at anything, and
  isolation is exactly the right response — hand it a single image and there is
  nothing left to confabulate from.
- **Binding question mismatched, canary fine → ONE fresh reader, same batch.**
  Only if that reader trips too do you isolate. A binding mismatch has a second
  cause the canary does not: **the image may genuinely be ambiguous.** A view
  that is a blob at 48px gets inconsistent bump counts from anyone, and five
  readers each staring at one blob will not do better than one — they will just
  cost five times as much to fail. (The same class of thing already burned a
  release: in 1.3.0 the thumbnails were squashed square, so "wider or taller?"
  was unanswerable by construction and 21 of 52 reads were re-runs of a question
  nobody could get right.)

If the fresh reader trips on the SAME image, suspect the image before the
reader: check `thinnest_px48` and the view's own metrics before spending four
more readers on it.

## 4b. Gate 1 — RECOGNISED (any 3 of the 4 views)

Spawn a context-free reader agent (a fresh subagent given NOTHING but the images) with exactly this task:

> Look at these images one at a time, answering only from what you SEE.
> 1) [thumb24 of the identity view] What FEELING does this shape give — heavy/stable, fast/agile, sharp/menacing, floating? One phrase.
> 2-5) [thumb48 of front / side / top / hero] **Name your FIVE best guesses for what
>    this is, most likely first.** Then: what parts can you make out, and does this
>    view read as a build or as an abstract shape/nothing?

**The verdict is SCORED, not argued — and you do not do the scoring.** Hand over
the reader's words, verbatim and in order. The tool matches them against the
accepted list your brief wrote down before any geometry existed:

```bash
python3 harness/identity.py --brief brief.md --round N \
        --guesses side="t-rex,dinosaur,velociraptor,dragon,lizard" \
                  hero="four-legged animal,dog,goat,boar,bear"
```

**You may not decide that "hound" counts as "wolf" here.** That judgment is real
and it is yours — but it belongs in the brief, in writing, before the rounds are
paid for. If a reader keeps saying a word you think IS the creature, go and add
it to the identity slot and say why in the closing DEVLOG. Adding it at r3, with
a shape you have already built and want to keep, is grading your own thumbnails,
which iron law 2 forbids and which this tool no longer lets you do.

The rule it applies — **rank R passes if the noun reached R in at least R
views**:

| | needs |
|---|---|
| its FIRST guess in any one view | 1 view |
| second place | 2 views |
| third | 3 views |
| fourth | 4 views |

Fifth and beyond does not count: a noun that never gets above fifth is being
listed, not recognised. **And the requirement drops by one view per repair
round** (never below one), because another round costs more than the last while
buying no more information.

Synonyms count as the noun — buffalo for bison, wyrm for dragon. That judgment
is yours; it is the only one the tool leaves you.

Why scored rather than exact. A 48px silhouette is a genuinely ambiguous object,
and a one-word bar measures the reader's confidence as much as the creature's
clarity. Two shapes of read make the case. A first read can come back as *"a
generic bat/moth/gargoyle silhouette"* — the ordered noun IS in that sentence.
And a read of `front=Rabbit, side/top/hero=Scorpion` under an exact-match rule
fails on the front view alone, sending the creature back for round after round
that cannot improve the verdict, because the rule cannot express "three of four
views named it first".

Every view answering "abstract / nothing / a stick" scores nothing and fails.
"A fox" plus four other canines when the order said dragon is still a failed
gate — the customer asked for a dragon and would receive a fox. Feel mismatch →
repair. The verdict IS the work order. Budget: 2 repair rounds, then iron law 3
(concept restart).

Record the rank, not just the pass: `--record rN ID pass "scorpion (rank 3)"`.
Rank 3 or 4 is a pass that still says something — the creature reads, but not
loudly — and that belongs in the closing line.

### On a FAIL: look before you rebuild

A pole is read on ONE view. When it fails, **the next thing you spend is three
more images, not a round.** Show the same shape's other three views to one
reader before you change a single number. A reader's price is the subagent
existing, not the pictures inside it, so three more images is a fraction of a
round — and a rebuild is a whole one that may be throwing away a shape which
already works from a different angle. `identity.py` prints this on every FAIL
and names the views you have not tried.

The failure this prevents: a creature fails on its declared view, gets
rebuilt, fails again, gets its concept restarted — and then reads first-guess
from a view nobody had tried. The creature was never wrong. **The declared
identity view was.**

If another view reads, do not celebrate and move on quietly — **change the
brief's identity view and write down why.** Gate 1's 3-of-4 rule holds you to
the view the brief names, so the brief has to name the one that works. Some
forms simply have no good side view: anything wide and sprawling is recognised
from above, and anything wearing architecture reads as architecture in profile.

### A round builds TWO OPPOSITE POLES, not one attempt and not three near-copies

Do not spend a whole round-trip trying one answer and finding out it was wrong.
Build **two versions that are opposite ends of ONE named axis**, and put both in
front of the same reader.

```bash
python3 harness/round.py out rN --spec pole_a.json pole_b.json --prev r<N-1> --gate ID
#   ... read them, then promote the winner so the iou chain follows it:
python3 harness/round.py out rN --promote v2
```

A build and a view are seconds; a reader is the expensive unit and its price is
almost entirely the subagent existing. Two versions cost seconds more than one.
Two separate reads cost twice a read. **The expensive unit is the round trip,
not the work inside it.**

**Name the axis, then push BOTH ends of it.** The brief's one exaggeration stays
fixed; what changes is how it is STAGED:

| axis | pole A | pole B |
|---|---|---|
| coiled / released | energy stored, wound back | mid-strike, thrown forward |
| reared / flattened | tall, weight on the hind | long and low, weight forward |
| front-loaded / rear-loaded | mass in the head and chest | mass in the tail and haunches |
| gathered / sprawled | limbs tucked, one silhouette | limbs thrown wide |

Two poles of one axis beat three vague "different attempts". The trap is that
`iou_vs_prev` cannot tell you which you have: it counts overlapping pixels, so
changing proportions moves it a long way while the design stands still. The
ruler that separates designs is where the mass sits, in thirds of the frame.

`round.py` measures this before the reader is spawned and **refuses to let you
spend one on two copies of the same idea** (floor 0.25, `harness/brief.py`). It
also warns when a pole's `fill` — the share of its own bounding box that is
solid — comes back high: an "opposite pose" that folds the signature part flat
onto the body has not restaged the creature, it has erased it.

**Poles are judged on the IDENTITY VIEW ONLY — one image each.** Not four views
each, and not the hero unless the brief named the hero. The batch is two
identity views plus the canary: three images, not thirteen.

This is what `outline.py` bought. Everything the other three views were being
shown for — aspect, protrusion count, thinnest feature, how much the shape moved
— is now computed from the geometry, exactly, for free, before the reader is
spawned. The only thing left that needs eyes is *what does this look like*, and
that question is asked of the view the brief declared would carry the creature.

The reason: a reader's price is the subagent existing, not the pictures, and
three quarters of the pictures were being shown to ask questions a script can
answer.

**ONE reader does the whole gate — choosing AND confirming, in one batch.**
This used to be two: one to pick the winner, then a second to confirm 3-of-4 on
it. Send all of it at once instead — each pole's identity view, plus all four
views of BOTH poles, plus the canary:

> Images A-J are silhouettes. For each: what is it? (five best guesses, most
> likely first). Then: taking A and F as two whole creatures, which reads more
> strongly, and why? One line each.

Then score the winner's four views with `identity.py`. **You already have the
answers for the loser too; you simply throw them away.**

Why this inverts the old advice. When a read carried thirteen images, images
were the unit and the rule was "send fewer". Now each read carries one or two,
Why this inverts the old advice. When a read carried thirteen images, images
were the unit and the rule was "send fewer". Now each read carries one or two,
and the balance flips: almost the whole reader bill is the subagent EXISTING,
so the rule is now the opposite — **as few readers as possible, as many images
in each as the question needs.** Merging choose+confirm takes a three-round gate
from six readers to three.

A pole whose noun matches the brief wins outright. If neither matches, two nouns
from two OPPOSITE stagings is a far better work order than one "no" — the axis
you named is the thing being tested, so the answer tells you which end of it is
warmer. Two near-copies cannot tell you that, which is why `round.py` refuses to
read them.

**Promote the winner** (`--promote v2`) so the winning pole becomes that round's
output and the next round's `--prev` follows it. Record ONE verdict for the
round.

This is not a new mechanism: Gate 2 below has always shown one reader two
silhouettes and asked which is punchier. This applies the same move to Gate 1,
where changing one variable at a time — claw height, then tail angle, then leg
thickness — costs a full round to learn one thing.

**Three of the four views must read. The fourth may be a dead view.** This gate
used to demand all four, and a demand for a view that carries nothing spends
readers on a repair that cannot work: a low, wide, shelled creature with a
central mast is a blob from the front at 48px, and no amount of repair changes
what an outline of that shape looks like head-on. Some forms have
one axis that carries nothing — that is a property of the form, not a defect in
the build.

So: **count the views that read. Three or more = pass.** Two or fewer = fail, and
the repair goes to the biggest shapes, not to the weakest view. One more rule
comes with the allowance: **the identity view named in the brief is not
optional** — if the view you declared would carry this creature is the one that
reads as "a blob", that is a failed gate no matter what the other three say. You
chose that view; the gate holds you to it.

Write the count into the record so the budget is visible:
`--record rN ID pass|fail "<noun>"` and put `3/4` or `2/4` at the front of the noun.

**Ask permission BEFORE the reader, record after it — every time:**

```bash
#   round.py already ran --preflight and told you whether to spawn a reader.
#   After the read:
python3 harness/roundcheck.py out --record rN ID pass|fail "<the reader's noun, verbatim>"
python3 harness/roundcheck.py out --check ID          # BLOCK: = no third tweak, restart
```

The pre-check exists because the order used to be wrong: the reader was spent
FIRST and the question "was that round allowed?" came afterwards, so the thing
that stops waste sat behind the thing that costs money. **A reader is not
free** — a subagent's price is almost entirely the cost of it EXISTING, not the
pictures inside it.

`--preflight` refuses a read the machine can already answer: a silhouette more
than 0.85 similar to the one a reader rejected last round will get the same
noun back, and a feature under 3px cannot be reported on by anyone. Neither is
a judgment call — change the shape first, then pay for the read.

Law 3 used to be yours to apply to yourself, and self-applied it does not hold:
a run will nudge the same feature through many failing rounds before restarting.
`roundcheck` counts instead, and it counts what the round *produced*: a repair
that leaves the silhouette more than 0.85 similar to the last one is a nudge
whatever you call it. Two of those and the third is refused. This is the same
move as law 2 — the thing you cannot be trusted to judge about your own work is
taken out of your hands — and it applies to Gate 2 identically (`PUNCH`).

## 5. Gate 2 — PUNCHIER (after Gate 1; the same 3-of-4 must stay readable; reader verification from §4 applies — randomize which of prev/current is IMG-A)

LOW is not done when it's recognisable — it's done when it's EXAGGERATED. A push
may ONLY make the silhouette bolder: push the extreme proportion further, harden
breaks, deepen negative space, exaggerate the signature.

**Build THREE different pushes and judge them together against the incumbent —
in ONE round, not three.** Same command as §4b:

```bash
python3 harness/round.py out rN --spec push_a.json push_b.json push_c.json \
                          --prev r<N-1> --gate PUNCH
```

Show the reader the incumbent plus all three pushes, shuffled, labels neutral:

> These are silhouettes of a creature. Which is punchiest — most striking, most
> tension? Rank them, one sentence why for the winner.

Incumbent wins = LOW locks where it is, done. A push wins and three views still
read (the identity view among them) = promote it, skeleton and main volumes lock.

**Why this is one round and not three.** Spending one round per push means each
push is a full round trip to learn one thing, and the
answer never moved. Put side by side in one read, the same three pushes cost one
round-trip and the ranking says WHICH direction was warmest instead of only that
each was colder than the incumbent.

**ONE round. If the incumbent wins, this gate is FINISHED — do not run a second.**

That is not a budget cut, it is what the verdict means. Three pushes built in
three different directions, judged side by side by a reader who knows nothing,
and the reader still picked what you already had: the silhouette is punchy
enough. There is no information left to buy. A second round asks the same
question with three more guesses and gets the same answer — the
field build ran that experiment for us, three rounds and four readers, every one
reverting to r1, three different readers naming the same silhouette.

A second round is allowed in exactly one case: **a push won on punch but broke
the 3-of-4 read.** Then you have a direction that works and a legibility problem
to fix, which is a real work order. Anything else — stop, lock r1, move to MID.

## 6. Outputs

`spec.json`, `creature.glb`, `out/rN/` per round (sils, thumbs, metrics, archived spec), reader verdicts verbatim in `log.md`.
