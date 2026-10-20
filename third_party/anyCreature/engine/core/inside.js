// ACS engine — where is this point, relative to that volume's surface?
//
// ONE answer, one definition. There used to be three copies of a wrong one.
//
// THE BUG. root_containment, limb_clearance and part_attachment each asked the
// same question — "is this point inside that volume, and if not, how far out?" —
// and each answered it the same way: find the nearest ring CENTRE, then compare
// against a SPHERE whose radius is that ring's widest point.
//
//     const rad = Math.max(...ring.map(q => G.len(G.sub(q, c2))));
//     if (best < rad * 0.98) inside++;
//
// A volume is not a string of spheres. It is a lofted tube, and the sphere
// bulges past the tube's END CAP by the ring's own half-width. The error is
// therefore WORST EXACTLY WHERE IT MATTERS MOST: at the end of a chain, which is
// where the next chain attaches. A torso 0.80 m wide hands out 0.4 m of free
// "inside" straight off the front of the chest — so a head sitting 0.33 m in
// front of the last ring centre was declared buried, while its real distance to
// the surface was 0.24 m. It floated. Nothing caught it, because all three
// checks that could have were reading the same wrong shape.
//
// THE FIX is not a better radius. Any radius is still a sphere. This measures
// against the actual triangles the file will ship.
//
// SIGN WITHOUT A CLOSED MESH. Ray casting needs a closed surface and these are
// not closed: an attached chain leaves its root ring open on purpose, because it
// is meant to be buried. So the sign comes from the surface itself — take the
// closest point, and ask which side of ITS triangle the query point is on. Local,
// exact for a smooth tube, and indifferent to holes elsewhere in the mesh.
'use strict';

function tris(mesh) {
  // faces may be quads (the loft emits rings of quads); fan them once and keep it
  if (mesh._sdT) return mesh._sdT;
  const out = [];
  for (const f of mesh.F) {
    if (f.length === 3) out.push(f);
    else for (let i = 1; i + 1 < f.length; i++) out.push([f[0], f[i], f[i + 1]]);
  }
  const V = mesh.V;
  const T = out.map(t => {
    const a = V[t[0]], b = V[t[1]], c = V[t[2]];
    const ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    const ac = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
    const n = [ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0]];
    const L = Math.hypot(n[0], n[1], n[2]) || 1;
    const cen = [(a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3, (a[2] + b[2] + c[2]) / 3];
    const rad = Math.max(Math.hypot(a[0] - cen[0], a[1] - cen[1], a[2] - cen[2]),
                         Math.hypot(b[0] - cen[0], b[1] - cen[1], b[2] - cen[2]),
                         Math.hypot(c[0] - cen[0], c[1] - cen[1], c[2] - cen[2]));
    return { a, b, c, n: [n[0] / L, n[1] / L, n[2] / L], cen, rad };
  });
  Object.defineProperty(mesh, '_sdT', { value: T, enumerable: false });
  return T;
}

// closest point on a triangle to p — the standard region test, no iteration
function closestOnTri(p, T) {
  const { a, b, c } = T;
  const ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const ac = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
  const ap = [p[0] - a[0], p[1] - a[1], p[2] - a[2]];
  const d1 = ab[0] * ap[0] + ab[1] * ap[1] + ab[2] * ap[2];
  const d2 = ac[0] * ap[0] + ac[1] * ap[1] + ac[2] * ap[2];
  if (d1 <= 0 && d2 <= 0) return a;
  const bp = [p[0] - b[0], p[1] - b[1], p[2] - b[2]];
  const d3 = ab[0] * bp[0] + ab[1] * bp[1] + ab[2] * bp[2];
  const d4 = ac[0] * bp[0] + ac[1] * bp[1] + ac[2] * bp[2];
  if (d3 >= 0 && d4 <= d3) return b;
  const vc = d1 * d4 - d3 * d2;
  if (vc <= 0 && d1 >= 0 && d3 <= 0) {
    const v = d1 / (d1 - d3);
    return [a[0] + ab[0] * v, a[1] + ab[1] * v, a[2] + ab[2] * v];
  }
  const cp = [p[0] - c[0], p[1] - c[1], p[2] - c[2]];
  const d5 = ab[0] * cp[0] + ab[1] * cp[1] + ab[2] * cp[2];
  const d6 = ac[0] * cp[0] + ac[1] * cp[1] + ac[2] * cp[2];
  if (d6 >= 0 && d5 <= d6) return c;
  const vb = d5 * d2 - d1 * d6;
  if (vb <= 0 && d2 >= 0 && d6 <= 0) {
    const w = d2 / (d2 - d6);
    return [a[0] + ac[0] * w, a[1] + ac[1] * w, a[2] + ac[2] * w];
  }
  const va = d3 * d6 - d5 * d4;
  if (va <= 0 && (d4 - d3) >= 0 && (d5 - d6) >= 0) {
    const w = (d4 - d3) / ((d4 - d3) + (d5 - d6));
    return [b[0] + (c[0] - b[0]) * w, b[1] + (c[1] - b[1]) * w, b[2] + (c[2] - b[2]) * w];
  }
  const den = 1 / (va + vb + vc), v = vb * den, w = vc * den;
  return [a[0] + ab[0] * v + ac[0] * w, a[1] + ab[1] * v + ac[1] * w, a[2] + ab[2] * v + ac[2] * w];
}

// Signed distance from p to the mesh surface. NEGATIVE means inside.
function signedDistance(p, mesh) {
  const T = tris(mesh);
  if (!T.length) return Infinity;
  // Cheap bound first: a triangle whose centroid is farther than (best + its own
  // radius) cannot beat the incumbent, so most of them never get the full test.
  let best = Infinity, hit = null;
  let bound = Infinity;
  for (const t of T) {
    const dc = Math.hypot(p[0] - t.cen[0], p[1] - t.cen[1], p[2] - t.cen[2]);
    if (dc - t.rad >= bound) continue;
    const q = closestOnTri(p, t);
    const d = Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
    if (d < best) { best = d; hit = t; bound = d; }
  }
  if (!hit) return Infinity;
  const q = closestOnTri(p, hit);
  const side = (p[0] - q[0]) * hit.n[0] + (p[1] - q[1]) * hit.n[1] + (p[2] - q[2]) * hit.n[2];
  return side < 0 ? -best : best;
}

const isInside = (p, mesh) => signedDistance(p, mesh) < 0;

const bbox = (m) => {
  if (m._sdBox) return m._sdBox;
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const v of m.V) for (let k = 0; k < 3; k++) {
    if (v[k] < lo[k]) lo[k] = v[k];
    if (v[k] > hi[k]) hi[k] = v[k];
  }
  const b = { lo, hi };
  Object.defineProperty(m, '_sdBox', { value: b, enumerable: false });
  return b;
};

// Do these two pieces of geometry actually share space? Not "are they close" —
// close is a seam, and a seam is fine. Joined means one has material inside the
// other, by enough that it is a weld and not a graze.
function joined(a, b, tol, weld) {
  const A = bbox(a), B = bbox(b);
  for (let k = 0; k < 3; k++) if (A.lo[k] - tol > B.hi[k] || B.lo[k] - tol > A.hi[k]) return false;
  const step = x => Math.max(1, Math.ceil(x.V.length / 240));
  let n = 0;
  for (const [x, y] of [[a, b], [b, a]]) {
    const s = step(x);
    for (let i = 0; i < x.V.length; i += s) if (signedDistance(x.V[i], y) < tol && ++n >= weld) return true;
  }
  return false;
}

// One body, or several? A creature is a THING, and a thing is connected. This
// exists because a head once shipped as a free-floating object 0.24 m clear of
// the neck, held to the creature only by two stray vertices of a foreleg that
// happened to poke into it. Every check that could have caught that was reading
// a sphere instead of a surface — but even with the surface, "is this seated in
// its host" is a local question, and the failure was global. So ask the global
// question too: walk the pieces, union the ones that share material, and refuse
// anything that comes back as more than one island. `weld` is why two stray
// vertices do not count as an attachment.
function islands(meshes, tol, weld = 3) {
  const ms = meshes.filter(m => m.V && m.V.length && m.F && m.F.length);
  const parent = ms.map((_, i) => i);
  const find = i => { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; };
  const union = (i, j) => { const a = find(i), b = find(j); if (a !== b) parent[a] = b; };
  for (let i = 0; i < ms.length; i++)
    for (let j = i + 1; j < ms.length; j++)
      if (find(i) !== find(j) && joined(ms[i], ms[j], tol, weld)) union(i, j);
  const groups = new Map();
  ms.forEach((m, i) => {
    const r = find(i);
    if (!groups.has(r)) groups.set(r, []);
    groups.get(r).push(m.part || m.chain || '?');
  });
  return [...groups.values()].sort((a, b) => b.length - a.length);
}

module.exports = { signedDistance, isInside, islands };
