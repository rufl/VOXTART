// ACS engine — self-collision over the animation (broad phase spheres, narrow phase mesh).
//
// Cost was the objection to doing this at all; it is not real. Sphere proxies bound to
// the dominant bone move with a matrix multiply and never touch the skinning path, so the
// broad phase is O(spheres^2 x frames) and independent of vertex count. Only the pairs the
// broad phase names get skinned. A whole body comes to a few hundred spheres and tens
// of thousands of candidate pairs, of which a few dozen reach the narrow phase; all
// three clips together stay well under a second.
//
// Two filters keep it honest:
//   - joints within 3 hops on the skeleton are skipped; neighbouring parts touch by design
//   - pairs that already overlap in the BIND pose are skipped; a brace bolted to the back
//     is a static question and belongs to part_overlap, not to the animation
'use strict';
const { skinVerts, jointWorlds } = require('./checks.js');

const mul=(a,b)=>{const o=new Array(16).fill(0);
  for(let c=0;c<4;c++)for(let r=0;r<4;r++)for(let k=0;k<4;k++)o[c*4+r]+=a[k*4+r]*b[c*4+k];return o;};
const apply=(m,v)=>[m[0]*v[0]+m[4]*v[1]+m[8]*v[2]+m[12],
                    m[1]*v[0]+m[5]*v[1]+m[9]*v[2]+m[13],
                    m[2]*v[0]+m[6]*v[1]+m[10]*v[2]+m[14]];
function inv(m){ // 剛性矩陣的反矩陣：R^T 與 -R^T·t
  const r=[m[0],m[1],m[2],m[4],m[5],m[6],m[8],m[9],m[10]];
  const t=[m[12],m[13],m[14]];
  const o=[r[0],r[3],r[6],0, r[1],r[4],r[7],0, r[2],r[5],r[8],0, 0,0,0,1];
  o[12]=-(r[0]*t[0]+r[1]*t[1]+r[2]*t[2]);
  o[13]=-(r[3]*t[0]+r[4]*t[1]+r[5]*t[2]);
  o[14]=-(r[6]*t[0]+r[7]*t[1]+r[8]*t[2]);
  return o; }

function build(spec){
  const s=JSON.parse(JSON.stringify(spec));
  require(E+'relative.js').resolveJoints(s);
  const sk=buildSkeleton(s), meshes=compile(s);
  try{ require(E+'compile.js').drainInfo(); }catch(e){}
  const locals=localTranslations(sk);
  // 骨架跳數
  const N=sk.joints.length, par=sk.joints.map(j=>j.parent);
  const depth=i=>{let d=0,c=i; while(par[c]>=0&&d<64){c=par[c];d++;} return d;};
  const hop=(a,b)=>{ let x=a,y=b,d=0; let dx=depth(x),dy=depth(y);
    while(dx>dy){x=par[x];dx--;d++;} while(dy>dx){y=par[y];dy--;d++;}
    while(x!==y&&d<64){x=par[x];y=par[y];d+=2;} return d; };
  // 球體代理
  const rest=jointWorlds(sk,locals,{name:'_',tracksResolved:{}},0);
  const restInv=rest.map(inv);
  const balls=[];
  for(const m of meshes){
    const grp=new Map();
    for(let i=0;i<m.V.length;i++){
      const w=m.skin[i]; if(!w||!w.length) continue;
      let bn=w[0][0],bw=w[0][1];
      for(const [n,q] of w) if(q>bw){bn=n;bw=q;}
      const bi=sk.index[bn]; if(bi==null) continue;
      if(!grp.has(bi)) grp.set(bi,[]); grp.get(bi).push(m.V[i]);
    }
    for(const [bi,P] of grp){
      if(P.length<3) continue;
      // 沿「兩個最長軸」切格子，不是只切一軸。
      // 只切一軸的話，翼膜那種又寬又薄的面會變成一顆超大球（半徑＝對角線一半），
      // 兩片翅膀根本沒碰到也會被判成穿透。切成近似立方的格子才不會灌水。
      let lo=[1e9,1e9,1e9],hi=[-1e9,-1e9,-1e9];
      for(const p of P) for(let k=0;k<3;k++){ if(p[k]<lo[k])lo[k]=p[k]; if(p[k]>hi[k])hi[k]=p[k]; }
      const ext=[hi[0]-lo[0],hi[1]-lo[1],hi[2]-lo[2]];
      const ord=[0,1,2].sort((a,b)=>ext[b]-ext[a]);
      const cell=Math.max(ext[ord[2]], Math.max(...ext)/6, 1e-3);   // 目標格子邊長 ≈ 最短邊
      const nb=[1,1,1];
      for(const k of [ord[0],ord[1]]) nb[k]=Math.max(1,Math.min(6,Math.round(ext[k]/cell)));
      const bins=new Map();
      for(const p of P){
        const key=[0,1,2].map(k=>Math.min(nb[k]-1,Math.floor(((p[k]-lo[k])/(ext[k]||1))*nb[k]))).join(',');
        if(!bins.has(key)) bins.set(key,[]); bins.get(key).push(p);
      }
      for(const B of bins.values()){ if(B.length<2) continue;
        const c=[0,0,0]; for(const p of B) for(let k=0;k<3;k++) c[k]+=p[k]/B.length;
        let r=0; for(const p of B){ const d=Math.hypot(p[0]-c[0],p[1]-c[1],p[2]-c[2]); if(d>r)r=d; }
        balls.push({ bone:bi, mesh:`${m.material}@${m.chain||m.part||'?'}`,
                     c:apply(restInv[bi],c), r });
      }
    }
  }
  return {s,sk,locals,meshes,balls,hop,anims:resolveAnims(s),rest};
}

function sweep(ctxo, frames=32, hopMin=4){
  const {sk,locals,balls,hop,anims,rest}=ctxo;
  // 先量 bind 姿勢的間距當基準線。本來就貼在一起的（背上的托架、殼縫的甲片）
  // 不是動畫的錯 —— 那是 part_overlap / touch 管的靜態問題。
  // 這裡只問一件事：動畫有沒有把它們壓得比休息姿勢更深。
  const n=balls.length, pairs=[];
  const R=balls.map(b=>apply(rest[b.bone],b.c));
  for(let i=0;i<n;i++) for(let j=i+1;j<n;j++){
    if(balls[i].mesh===balls[j].mesh) continue;
    if(hop(balls[i].bone,balls[j].bone)<hopMin) continue;
    const d0=Math.hypot(R[i][0]-R[j][0],R[i][1]-R[j][1],R[i][2]-R[j][2])-balls[i].r-balls[j].r;
    pairs.push([i,j,d0]);
  }
  const meshByName=new Map();
  for(const m of ctxo.meshes) meshByName.set(`${m.material}@${m.chain||m.part||'?'}`, m);
  const out=[];
  for(const a of anims){
    // ── 粗相：球體。只負責點名「這一幀這兩塊可能撞」，不下結論。
    const cand=new Map();     // "meshA|meshB" -> Set(frame)
    for(let f=0;f<=frames;f++){
      const W=jointWorlds(sk,locals,a,f/frames);
      const P=balls.map(b=>apply(W[b.bone],b.c));
      for(const [i,j,d0] of pairs){
        const d=Math.hypot(P[i][0]-P[j][0],P[i][1]-P[j][1],P[i][2]-P[j][2])-balls[i].r-balls[j].r;
        if(d-Math.min(d0,0) >= 0) continue;
        const k=[balls[i].mesh,balls[j].mesh].sort().join('|');
        if(!cand.has(k)) cand.set(k,new Set()); cand.get(k).add(f);
      }
    }
    // ── 窄相：只對被點名的配對＋幀，算真正的網格最近距離。
    let worst=1e9, info=null, narrow=0;
    const cacheV=new Map();
    const vertsAt=(m,f)=>{ const k=m.material+'|'+(m.chain||m.part)+'|'+f;
      if(!cacheV.has(k)) cacheV.set(k, skinVerts(m,sk,jointWorlds(sk,locals,a,f/frames)));
      return cacheV.get(k); };
    for(const [k,fs2] of cand){
      const [na,nb2]=k.split('|'); const ma=meshByName.get(na), mb=meshByName.get(nb2);
      if(!ma||!mb) continue;
      const step=Math.max(1,Math.ceil(Math.max(ma.V.length,mb.V.length)/60));
      // 先量休息姿勢的真實間距當基準
      let rest0=1e9;
      { const A=vertsAt(ma,0), B=vertsAt(mb,0); }   // 觸發 cache（用第 0 幀近似不準，改用 restV）
      const RA=skinVerts(ma,sk,rest), RB=skinVerts(mb,sk,rest);
      for(let i=0;i<RA.length;i+=step) for(let j=0;j<RB.length;j+=step){
        const d=Math.hypot(RA[i][0]-RB[j][0],RA[i][1]-RB[j][1],RA[i][2]-RB[j][2]); if(d<rest0)rest0=d; }
      for(const f of fs2){
        const A=vertsAt(ma,f), B=vertsAt(mb,f);
        let mn=1e9;
        for(let i=0;i<A.length;i+=step) for(let j=0;j<B.length;j+=step){
          const d=Math.hypot(A[i][0]-B[j][0],A[i][1]-B[j][1],A[i][2]-B[j][2]); if(d<mn)mn=d; }
        narrow++;
        const rel=mn-Math.min(rest0,1e9);
        if(rel<worst){ worst=rel; info={t:+(f/frames).toFixed(2),a:na,b:nb2,rest:+rest0.toFixed(3),abs:+mn.toFixed(3)}; }
      }
    }
    out.push({clip:a.name, gap:+worst.toFixed(3), pairs:pairs.length, cand:cand.size, narrow, ...info});
  }
  return out;
}

function buildProxies(sk, meshes, restWorld) {
  const restInv = restWorld.map(inv);
  const balls = [];
  for (const m of meshes) {
    const grp = new Map();
    for (let i = 0; i < m.V.length; i++) {
      const w = m.skin[i]; if (!w || !w.length) continue;
      let bn = w[0][0], bw = w[0][1];
      for (const [n, q] of w) if (q > bw) { bn = n; bw = q; }
      const bi = sk.index[bn]; if (bi == null) continue;
      if (!grp.has(bi)) grp.set(bi, []); grp.get(bi).push(m.V[i]);
    }
    for (const [bi, P] of grp) {
      if (P.length < 3) continue;
      // Bin along the TWO longest axes so each cell is roughly cubic. Binning one axis
      // turns a wing membrane into one sphere of radius half its diagonal, and two wings
      // that never touch report a collision.
      let lo = [1e9, 1e9, 1e9], hi = [-1e9, -1e9, -1e9];
      for (const p of P) for (let k = 0; k < 3; k++) { if (p[k] < lo[k]) lo[k] = p[k]; if (p[k] > hi[k]) hi[k] = p[k]; }
      const ext = [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]];
      const ord = [0, 1, 2].sort((a, b) => ext[b] - ext[a]);
      const cell = Math.max(ext[ord[2]], Math.max(...ext) / 6, 1e-3);
      const nb = [1, 1, 1];
      for (const k of [ord[0], ord[1]]) nb[k] = Math.max(1, Math.min(6, Math.round(ext[k] / cell)));
      const bins = new Map();
      for (const p of P) {
        const key = [0, 1, 2].map(k => Math.min(nb[k] - 1, Math.floor(((p[k] - lo[k]) / (ext[k] || 1)) * nb[k]))).join(',');
        if (!bins.has(key)) bins.set(key, []); bins.get(key).push(p);
      }
      for (const B of bins.values()) {
        if (B.length < 2) continue;
        const c = [0, 0, 0]; for (const p of B) for (let k = 0; k < 3; k++) c[k] += p[k] / B.length;
        let r = 0; for (const p of B) { const d = Math.hypot(p[0] - c[0], p[1] - c[1], p[2] - c[2]); if (d > r) r = d; }
        balls.push({ bone: bi, mesh: `${m.material}@${m.chain || m.part || '?'}`, c: apply(restInv[bi], c), r });
      }
    }
  }
  return balls;
}

function selfClip(spec, sk, locals, meshes, anims, frames = 32, hopMin = 4, restMin = 0) {
  const rest = jointWorlds(sk, locals, { name: '_', tracksResolved: {} }, 0);
  const balls = buildProxies(sk, meshes, rest);
  const par = sk.joints.map(j => j.parent);
  const depth = i => { let d = 0, c = i; while (par[c] >= 0 && d < 64) { c = par[c]; d++; } return d; };
  const hop = (a, b) => { let x = a, y = b, d = 0, dx = depth(x), dy = depth(y);
    while (dx > dy) { x = par[x]; dx--; d++; } while (dy > dx) { y = par[y]; dy--; d++; }
    while (x !== y && d < 64) { x = par[x]; y = par[y]; d += 2; } return d; };
  const R = balls.map(b => apply(rest[b.bone], b.c));
  const pairs = [];
  for (let i = 0; i < balls.length; i++) for (let j = i + 1; j < balls.length; j++) {
    if (balls[i].mesh === balls[j].mesh) continue;
    if (hop(balls[i].bone, balls[j].bone) < hopMin) continue;
    const d0 = Math.hypot(R[i][0] - R[j][0], R[i][1] - R[j][1], R[i][2] - R[j][2]) - balls[i].r - balls[j].r;
    // Only pairs that start FAR APART. Fingers on the same wing, a membrane and the arm
    // that carries it, a neck and the chest it grows out of — all sit at ~0 m at rest and
    // will "close" the moment anything moves. The defect this check exists for looks
    // different: something that was a metre away arrives at a millimetre.
    if (d0 < restMin) continue;
    pairs.push([i, j, d0]);
  }
  const byName = new Map(); for (const m of meshes) byName.set(`${m.material}@${m.chain || m.part || '?'}`, m);
  const out = [];
  for (const a of anims) {
    const cand = new Map();
    for (let f = 0; f <= frames; f++) {
      const W = jointWorlds(sk, locals, a, f / frames);
      const P = balls.map(b => apply(W[b.bone], b.c));
      for (const [i, j, d0] of pairs) {
        const d = Math.hypot(P[i][0] - P[j][0], P[i][1] - P[j][1], P[i][2] - P[j][2]) - balls[i].r - balls[j].r;
        if (d >= 0) continue;
        const k = [balls[i].mesh, balls[j].mesh].sort().join('|');
        if (!cand.has(k)) cand.set(k, new Set()); cand.get(k).add(f);
      }
    }
    let worst = Infinity, info = null;
    for (const [k, fs] of cand) {
      const [na, nb] = k.split('|'); const ma = byName.get(na), mb = byName.get(nb);
      if (!ma || !mb) continue;
      const step = Math.max(1, Math.ceil(Math.max(ma.V.length, mb.V.length) / 60));
      const RA = skinVerts(ma, sk, rest), RB = skinVerts(mb, sk, rest);
      let rest0 = Infinity;
      for (let i = 0; i < RA.length; i += step) for (let j = 0; j < RB.length; j += step) {
        const d = Math.hypot(RA[i][0] - RB[j][0], RA[i][1] - RB[j][1], RA[i][2] - RB[j][2]); if (d < rest0) rest0 = d; }
      // Re-apply the "far apart at rest" filter on the TRUE mesh distance. Spheres
      // over-approximate, so a pair can clear the broad-phase filter and still be two
      // things that touch in the bind pose.
      if (rest0 < restMin) continue;
      for (const f of fs) {
        const W = jointWorlds(sk, locals, a, f / frames);
        const A = skinVerts(ma, sk, W), B = skinVerts(mb, sk, W);
        let mn = Infinity;
        for (let i = 0; i < A.length; i += step) for (let j = 0; j < B.length; j += step) {
          const d = Math.hypot(A[i][0] - B[j][0], A[i][1] - B[j][1], A[i][2] - B[j][2]); if (d < mn) mn = d; }
        if (mn < worst) { worst = mn; info = { t: f / frames, a: na, b: nb, rest: rest0 }; }
      }
    }
    out.push({ clip: a.name, gap: worst === Infinity ? null : worst, ...(info || {}) });
  }
  return out;
}
module.exports = { selfClip };
