// ACS engine — skeleton builder. Author: Ariescar.
// Builds the joint tree from spec (chains + attach + mirror), computes local
// bind transforms and inverse bind matrices for GLB skinning.
'use strict';
const G = require('./geometry.js');

function mirrorName(n) { return n.startsWith('L') ? 'R' + n.slice(1) : n; }

// Returns { joints: [{name, pos, parent(index|-1)}], index: {name->i}, mirroredChains: {Lname->Rname} }
function buildSkeleton(spec) {
  const joints = []; const index = {};
  const addJoint = (name, pos, parentName) => {
    if (name in index) return index[name];
    const pi = parentName == null ? -1 : index[parentName];
    if (parentName != null && pi === undefined) throw new Error(`joint "${name}" parent "${parentName}" not yet defined`);
    index[name] = joints.length;
    joints.push({ name, pos: pos.slice(), parent: pi === undefined ? -1 : pi });
    return index[name];
  };

  const attach = spec.attach || {};
  // 1) axis-like chains first (those not attached to anything), in spec order
  const chainNames = Object.keys(spec.chains);
  const rootChains = chainNames.filter(c => !(c in attach));
  const subChains = chainNames.filter(c => c in attach);
  if (rootChains.length === 0) throw new Error('need at least one root chain (not listed in "attach")');
  for (const cn of rootChains) {
    const names = spec.chains[cn];
    // root chain: middle-out parenting is overkill; parent sequentially from first
    addJoint(names[0], spec.joints[names[0]], null);
    for (let i = 1; i < names.length; i++) addJoint(names[i], spec.joints[names[i]], names[i - 1]);
  }
  // 2) attached chains (legs etc.): root parents to the attach joint
  for (const cn of subChains) {
    const names = spec.chains[cn];
    const host = attach[cn];
    if (!(host in index)) throw new Error(`chain "${cn}" attaches to unknown joint "${host}"`);
    addJoint(names[0], spec.joints[names[0]], host);
    for (let i = 1; i < names.length; i++) addJoint(names[i], spec.joints[names[i]], names[i - 1]);
  }
  // 3) mirrored chains: create R* joints mirrored across X
  const mirroredChains = {};
  for (const cn of spec.mirror || []) {
    const names = spec.chains[cn];
    const rn = mirrorName(cn);
    mirroredChains[cn] = rn;
    const host = attach[cn]; // same host (axis joints sit on X=0)
    const rnames = names.map(mirrorName);
    const ov = spec.joints_R || {};   // optional per-joint right-side pose override
    const mpos = n => {
      const rn = mirrorName(n);
      if (ov[rn]) return ov[rn].slice();
      const p = spec.joints[n]; return [-p[0], p[1], p[2]];
    };
    addJoint(rnames[0], mpos(names[0]), host ?? null);
    for (let i = 1; i < rnames.length; i++) addJoint(rnames[i], mpos(names[i]), rnames[i - 1]);
    spec.chains[rn] = rnames; // register for animation mirroring
  }
  // 4) loose joints (hosts for parts, e.g. ears): not in any chain.
  //    Must have an "attach" entry. L* loose joints get auto-mirrored R twins.
  const inChains = new Set(chainNames.flatMap(c => spec.chains[c]));
  for (const [name, pos] of Object.entries(spec.joints)) {
    if (index[name] !== undefined || inChains.has(name)) continue;
    const host = attach[name];
    if (!host) throw new Error(`loose joint "${name}" needs an "attach" entry (which joint it rides on)`);
    addJoint(name, pos, host);
    if (name.startsWith('L')) {
      const rn = 'R' + name.slice(1);
      addJoint(rn, (spec.joints_R || {})[rn]?.slice() || [-pos[0], pos[1], pos[2]], host);
    }
  }
  return { joints, index, mirroredChains };
}

// local translation = world - parentWorld (bind rotations are identity)
function localTranslations(sk) {
  return sk.joints.map(j => {
    if (j.parent < 0) return j.pos.slice();
    const p = sk.joints[j.parent].pos;
    return G.sub(j.pos, p);
  });
}

// inverse bind matrix = translate(-worldPos), column-major mat4
function inverseBindMatrices(sk) {
  const out = new Float32Array(sk.joints.length * 16);
  sk.joints.forEach((j, i) => {
    const m = [1,0,0,0, 0,1,0,0, 0,0,1,0, -j.pos[0], -j.pos[1], -j.pos[2], 1];
    out.set(m, i * 16);
  });
  return out;
}

// ── public bone names for export ──
// Side-limb joints ship under the convention  <L|R><ChainBase><instance><Tg>
// (LArm1Sh, RFrontLeg1Kn): side prefix, chain base, instance digit(s), then a
// two-letter joint tag. Internal spec names stay authoring-side; this map is
// applied only when writing the GLB, so animations/skins (index-based) are
// untouched. Every exported bone starting with L/R conforms to
// ^[LR][A-Z][A-Za-z]*\d+[A-Z][a-z]$ — a structural pattern, not a word list.
const JOINT_TAGS = [
  ['shoulder', 'Sh'], ['elbow', 'El'], ['wrist', 'Wr'], ['hand', 'Ha'], ['finger', 'Fg'],
  ['thumb', 'Tb'], ['hip', 'Hp'], ['knee', 'Kn'], ['ankle', 'An'], ['toe', 'To'],
  ['foot', 'Ft'], ['paw', 'Pw'], ['thigh', 'Th'], ['shin', 'Sn'], ['claw', 'Cl'],
  ['root', 'Rt'], ['mid', 'Md'], ['tip', 'Tp'], ['base', 'Bs'], ['end', 'En'],
];
function jointTag(raw, used) {
  const low = raw.toLowerCase();
  let tag = null;
  for (const [k, t] of JOINT_TAGS) if (low.includes(k)) { tag = t; break; }
  if (!tag || used.has(tag)) {
    const s = raw.replace(/^[LR]/, '').replace(/[^A-Za-z]/g, '') || 'Jt';
    tag = s[0].toUpperCase() + (s[1] || 'x').toLowerCase();
  }
  if (used.has(tag))  // positional fallback: Ja, Jb, ...
    for (let c = 97; c <= 122; c++) {
      const t2 = 'J' + String.fromCharCode(c);
      if (!used.has(t2)) { tag = t2; break; }
    }
  used.add(tag);
  return tag;
}
function exportNames(spec, sk) {
  const map = {}; const taken = new Set();
  const claim = (name) => {  // guarantee global uniqueness
    let n = name, bump = 2;
    while (taken.has(n)) n = name.replace(/(\d+)(?=[A-Z][a-z]$)/, () => String(bump++));
    taken.add(n);
    return n;
  };
  const baseCount = {};
  for (const cn of spec.mirror || []) {
    const m = cn.match(/^L([A-Za-z]*?)(\d*)$/);
    const rawBase = (m && m[1]) ? m[1] : 'Limb';
    const base = rawBase[0].toUpperCase() + rawBase.slice(1);
    let inst = (m && m[2]) ? m[2] : null;
    if (inst === null) { baseCount[base] = (baseCount[base] || 0) + 1; inst = String(baseCount[base]); }
    const used = new Set();
    for (const n of spec.chains[cn]) {
      const tag = jointTag(n, used);
      map[n] = claim(`L${base}${inst}${tag}`);
      map[mirrorName(n)] = claim(`R${base}${inst}${tag}`);
    }
  }
  // loose L*/R* joints (ears, eyes' hosts) and any stray side-prefixed name:
  // give them the same mechanical form so no exported bone breaks the pattern
  for (const j of sk.joints) {
    // only names in the side convention (L/R + capital) are limbs; axis bones
    // like "Rump" (lowercase second letter) pass through untouched
    if (map[j.name] || !/^[LR][A-Z]/.test(j.name)) continue;
    if (/^[LR][A-Z][A-Za-z]*\d+[A-Z][a-z]$/.test(j.name)) { map[j.name] = claim(j.name); continue; }
    const side = j.name[0];
    const rest = j.name.slice(1).replace(/[^A-Za-z]/g, '') || 'Part';
    const base = rest[0].toUpperCase() + rest.slice(1);
    map[j.name] = claim(`${side}${base}1${jointTag(rest, new Set())}`);
  }
  return map;
}

// ── joint_range — how far each joint is allowed to turn ──────────────────────
//
// Degrees, per axis, in the joint's OWN frame: the same frame and the same axis
// names (rx/ry/rz) the animation tracks already use, so a limit and a track are
// directly comparable and no second coordinate convention exists to get wrong.
//
// The numbers are AUTHORED, not discovered. Whoever writes the spec already
// knows a knee bends one way, an elbow does not twist and a jaw opens further
// than a wrist bends — that knowledge is free to write down and impossible to
// compute from geometry. What the author does NOT know is how thick THIS
// creature's thigh is, so the declared limit is treated as a claim about the
// body and checks.js sweeps it against the body.
//
// An axis a joint declares nothing for is LOCKED at 0, not free. A limit table
// with holes in it is a table that checks nothing, and the common case is one
// axis per joint anyway. "free" opts an axis out explicitly (a wheel, a rotor).
//
// The R side is GENERATED and must never be authored. Mirroring negates ry and
// rz — the same rule compileAnim applies to tracks — and negating an interval
// swaps its ends, so [-10, 90] becomes [-90, 10]. An author writing both sides
// by hand is the exact shape of the mirror bug this release fixed.
const RANGE_AXES = ['rx', 'ry', 'rz'];

function jointRanges(spec, sk) {
  const out = {};
  const norm = (jn, r) => {
    const o = {};
    for (const ax of RANGE_AXES) {
      const v = r[ax];
      if (v === undefined) { o[ax] = [0, 0]; continue; }   // unwritten = locked
      if (v === 'free') { o[ax] = null; continue; }        // explicitly unbounded
      if (!Array.isArray(v) || v.length !== 2 || !v.every(x => Number.isFinite(x)))
        throw new Error(`joint_range "${jn}".${ax}: expected [min, max] in degrees, or "free"`);
      o[ax] = [Math.min(v[0], v[1]), Math.max(v[0], v[1])];
    }
    return o;
  };
  for (const [jn, r] of Object.entries(spec.joint_range || {})) {
    if (!r || typeof r !== 'object') throw new Error(`joint_range "${jn}": expected an object of axes`);
    out[jn] = norm(jn, r);
  }
  // generate the R side for every declared L joint whose twin exists
  for (const jn of Object.keys(spec.joint_range || {})) {
    const rj = mirrorName(jn);
    if (rj === jn || out[rj]) continue;                       // not mirrored, or overridden
    if (sk && sk.index && sk.index[rj] === undefined) continue; // no such twin on this body
    const s = out[jn];
    out[rj] = { rx: s.rx && s.rx.slice(),
                ry: s.ry && [-s.ry[1], -s.ry[0]],
                rz: s.rz && [-s.rz[1], -s.rz[0]] };
  }
  return out;
}

module.exports = { buildSkeleton, localTranslations, inverseBindMatrices, mirrorName, exportNames,
                  jointRanges, RANGE_AXES };
