"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";

// Decorative only: a slowly turning graph of theses (hubs) and the markets that price them.
// Every couple of seconds a market "reprices" and a signal travels back to the core.

const CLUSTERS: { name: string; markets: [title: string, price: number][] }[] = [
  { name: "AI regulation", markets: [["Federal AI safety bill", 0.12], ["EU delays AI Act", 0.41], ["CA frontier-model law", 0.46], ["Chip export curbs", 0.57]] },
  { name: "Recession 2027", markets: [["NBER recession in '27", 0.27], ["Unemployment hits 5%", 0.33], ["Negative Q1 GDP", 0.21], ["Fed cuts in December", 0.62]] },
  { name: "Midterms 2026", markets: [["Democrats take House", 0.66], ["GOP holds Senate", 0.72], ["Divided Congress", 0.58], ["Turnout above 50%", 0.39]] },
  { name: "Crypto", markets: [["BTC above $150k", 0.23], ["Market-structure bill", 0.29], ["Stablecoins top $400B", 0.52], ["G7 Bitcoin reserve", 0.08]] },
  { name: "Climate & energy", markets: [["Hottest year on record", 0.37], ["COP31 transition pledge", 0.24], ["EV share tops 10%", 0.31], ["Brent above $80", 0.76]] },
  { name: "Trade", markets: [["Tariffs stay above 15%", 0.64], ["US–China trade deal", 0.18], ["Chip tariffs by June", 0.45]] },
];

const INK = new THREE.Color("#0A0A0A");
const NODE = new THREE.Color("#A9A69E");
const ACCENT = new THREE.Color("#FF5A1F");

type Kind = "core" | "hub" | "market";
type Node = { kind: Kind; pos: THREE.Vector3; size: number; hub: number; title?: string; price?: number; label?: Label };
type Label = { el: HTMLDivElement; price?: HTMLSpanElement; delta?: HTMLSpanElement; w: number; h: number; alpha: number; flash: number; dx: number; dy: number };
type Signal = { market: number; t0: number; move: number };

const SIGNAL_EVERY = 2.4;
const SIGNAL_LEN = 3.2;
const DIST = 8.2; // camera distance: leaves room for labels inside the frame
const EDGE = 56; // px band at the frame edge where the canvas and labels fade out

function rng(seed: number) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function randUnit(rnd: () => number) {
  const z = rnd() * 2 - 1, a = rnd() * Math.PI * 2, s = Math.sqrt(1 - z * z);
  return new THREE.Vector3(s * Math.cos(a), z, s * Math.sin(a));
}

const smooth = (a: number, b: number, x: number) => {
  const u = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return u * u * (3 - 2 * u);
};

function buildGraph() {
  const rnd = rng(11);
  const nodes: Node[] = [{ kind: "core", pos: new THREE.Vector3(), size: 0.1, hub: -1 }];
  const edges: [number, number][] = [];
  const links: [number, number][] = [];
  const hubIds: number[] = [];

  // Hubs on a fibonacci sphere so the clusters spread evenly.
  CLUSTERS.forEach((c, i) => {
    const y = 1 - ((i + 0.5) / CLUSTERS.length) * 2, r = Math.sqrt(1 - y * y), a = i * 2.39996;
    const dir = new THREE.Vector3(r * Math.cos(a), y * 0.8, r * Math.sin(a)).normalize();
    const hub = nodes.push({ kind: "hub", pos: dir.multiplyScalar(1.35 + rnd() * 0.25), size: 0.06, hub: i, title: c.name }) - 1;
    hubIds.push(hub);
    edges.push([0, hub]);
    c.markets.forEach(([title, price]) => {
      const out = nodes[hub].pos.clone().normalize();
      const dir = out.multiplyScalar(0.9).add(randUnit(rnd)).normalize();
      const pos = nodes[hub].pos.clone().addScaledVector(dir, 0.6 + rnd() * 0.35);
      edges.push([hub, nodes.push({ kind: "market", pos, size: 0.036, hub: i, title, price }) - 1]);
    });
  });

  // Correlation links: each market to its nearest neighbour in another cluster, when close enough.
  const markets = nodes.map((n, i) => i).filter((i) => nodes[i].kind === "market");
  for (const i of markets) {
    let best = -1, bestD = 1.3;
    for (const j of markets) {
      if (nodes[j].hub === nodes[i].hub) continue;
      const d = nodes[i].pos.distanceTo(nodes[j].pos);
      if (d < bestD) [best, bestD] = [j, d];
    }
    if (best > i) links.push([i, best]);
  }
  return { nodes, edges, links, hubIds, markets };
}

function makeLabel(n: Node): Label {
  const el = document.createElement("div");
  el.className = `kg-label kg-${n.kind}`;
  const t = document.createElement("span");
  t.textContent = n.title!;
  el.appendChild(t);
  let price: HTMLSpanElement | undefined, delta: HTMLSpanElement | undefined;
  if (n.price != null) {
    price = document.createElement("span");
    price.className = "kg-price";
    price.textContent = `${Math.round(n.price * 100)}¢`;
    el.appendChild(price);
    delta = document.createElement("span");
    delta.className = "kg-delta";
    el.appendChild(delta);
  }
  return { el, price, delta, w: 0, h: 0, alpha: 0, flash: 0, dx: 0, dy: 0 };
}

export function MarketGraph() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = ref.current!;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const { nodes, edges, links, hubIds, markets } = buildGraph();

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.domElement.className = "kg-canvas";
    host.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.fog = new THREE.Fog(0xffffff, 6.4, 10.8); // far side of the graph fades into the page
    const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 50);
    camera.position.set(0, 0, DIST);

    const tilt = new THREE.Group();
    const spin = new THREE.Group();
    tilt.rotation.x = 0.28;
    tilt.add(spin);
    scene.add(tilt);

    const segments = (pairs: [number, number][], color: string) => {
      const pts = pairs.flatMap(([a, b]) => [nodes[a].pos, nodes[b].pos]);
      const geo = new THREE.BufferGeometry().setFromPoints(pts);
      return new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color }));
    };
    const edgeLines = segments(edges, "#C9C6BE");
    const linkLines = segments(links, "#E6E4DE");
    spin.add(linkLines, edgeLines);

    const dotGeo = new THREE.SphereGeometry(1, 20, 14);
    const dotMat = new THREE.MeshBasicMaterial();
    const dots = new THREE.InstancedMesh(dotGeo, dotMat, nodes.length);
    const base = nodes.map((n) => (n.kind === "core" ? ACCENT : n.kind === "hub" ? INK : NODE));
    spin.add(dots);

    // Signal pulse: a small accent dot that walks market -> hub -> core.
    const pulse = new THREE.Mesh(dotGeo, new THREE.MeshBasicMaterial({ color: ACCENT }));
    pulse.visible = false;
    spin.add(pulse);

    const overlay = document.createElement("div");
    overlay.className = "kg-labels";
    host.appendChild(overlay);
    nodes.forEach((n) => {
      if (!n.title) return;
      n.label = makeLabel(n);
      overlay.appendChild(n.label.el);
    });
    const measure = () => nodes.forEach((n) => {
      if (n.label) [n.label.w, n.label.h] = [n.label.el.offsetWidth, n.label.el.offsetHeight];
    });

    let w = 0, h = 0;
    const resize = () => {
      w = host.clientWidth;
      h = host.clientHeight;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      // Keep the whole graph in frame on tall, narrow screens.
      camera.position.z = w < h ? DIST * Math.min(1.5, h / w) : DIST;
      camera.updateProjectionMatrix();
      measure();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(host);
    resize();
    document.fonts.ready.then(measure);

    const pointer = { x: 0, y: 0 };
    const onMove = (e: PointerEvent) => {
      const r = host.getBoundingClientRect();
      pointer.x = ((e.clientX - r.left) / r.width) * 2 - 1;
      pointer.y = ((e.clientY - r.top) / r.height) * 2 - 1;
    };
    window.addEventListener("pointermove", onMove);

    const signals: Signal[] = [];
    let nextSignal = 1.2;
    const mtx = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), color = new THREE.Color();
    const world = new THREE.Vector3(), ndc = new THREE.Vector3();

    const fire = (t: number) => {
      // Prefer a market on the near side so the viewer sees it reprice.
      const near = markets.filter((i) => world.copy(nodes[i].pos).applyMatrix4(spin.matrixWorld).z > 0.4 && !signals.some((g) => g.market === i));
      const pool = near.length ? near : markets;
      const market = pool[Math.floor(Math.random() * pool.length)];
      const move = (Math.random() < 0.5 ? -1 : 1) * (1 + Math.floor(Math.random() * 4));
      const n = nodes[market];
      n.price = Math.min(0.97, Math.max(0.03, n.price! + move / 100));
      signals.push({ market, t0: t, move });
      const lb = n.label!;
      lb.price!.textContent = `${Math.round(n.price * 100)}¢`;
      lb.delta!.textContent = `${move > 0 ? "▲" : "▼"}${Math.abs(move)}`;
      lb.w = lb.el.offsetWidth;
    };

    const frame = (t: number) => {
      spin.rotation.y = still ? 0.6 : t * 0.07;
      tilt.rotation.x += (0.28 + pointer.y * 0.12 - tilt.rotation.x) * 0.04;
      tilt.rotation.z += (-pointer.x * 0.06 - tilt.rotation.z) * 0.04;
      scene.updateMatrixWorld();

      if (!still && t > nextSignal) {
        fire(t);
        nextSignal = t + SIGNAL_EVERY * (0.7 + Math.random() * 0.6);
      }

      // Per-node glow from active signals: the market flashes first, its hub when the pulse lands.
      const glow = new Float32Array(nodes.length);
      const size = new Float32Array(nodes.length);
      pulse.visible = false;
      for (let k = signals.length - 1; k >= 0; k--) {
        const g = signals[k], u = (t - g.t0) / SIGNAL_LEN;
        if (u >= 1) {
          const lb = nodes[g.market].label!;
          lb.delta!.textContent = "";
          lb.w = lb.el.offsetWidth;
          signals.splice(k, 1);
          continue;
        }
        const hub = hubIds[nodes[g.market].hub];
        glow[g.market] = Math.max(glow[g.market], smooth(0, 0.06, u) * (1 - smooth(0.6, 1, u)));
        glow[hub] = Math.max(glow[hub], smooth(0.36, 0.42, u) * (1 - smooth(0.55, 0.8, u)));
        if (u > 0.08 && u < 0.7) {
          const a = u < 0.4 ? smooth(0.08, 0.4, u) : smooth(0.4, 0.7, u);
          const [from, to] = u < 0.4 ? [nodes[g.market].pos, nodes[hub].pos] : [nodes[hub].pos, nodes[0].pos];
          pulse.position.lerpVectors(from, to, a);
          pulse.scale.setScalar(0.022);
          pulse.visible = true;
        }
        nodes[g.market].label!.flash = glow[g.market];
      }

      nodes.forEach((n, i) => {
        const breathe = n.kind === "core" ? 1 + 0.08 * Math.sin(t * 1.6) : 1;
        size[i] = n.size * breathe * (1 + glow[i] * 0.7);
        s.setScalar(size[i]);
        mtx.compose(n.pos, q, s);
        dots.setMatrixAt(i, mtx);
        dots.setColorAt(i, color.copy(base[i]).lerp(ACCENT, glow[i]));
      });
      dots.instanceMatrix.needsUpdate = true;
      dots.instanceColor!.needsUpdate = true;

      renderer.render(scene, camera);

      // Screen position and radius of every labelled or label-blocking node.
      const focal = h / (2 * Math.tan((camera.fov * Math.PI) / 360));
      const scr = nodes.map((n, i) => {
        world.copy(n.pos).applyMatrix4(spin.matrixWorld);
        const z = world.z, r = (size[i] * focal) / (camera.position.z - z);
        ndc.copy(world).project(camera);
        return { x: (ndc.x * 0.5 + 0.5) * w, y: (-ndc.y * 0.5 + 0.5) * h, z, r };
      });

      // Labels: near side only, nearest first. Each tries its preferred side, then the opposite one,
      // and skips any spot that leaves the frame or covers a dot or a label already placed.
      const placed: [number, number, number, number][] = [];
      const fits = (b: [number, number, number, number], self: number) =>
        b[0] > 4 && b[1] > 4 && b[2] < w - 4 && b[3] < h - 4 &&
        placed.every((p) => b[2] < p[0] || b[0] > p[2] || b[3] < p[1] || b[1] > p[3]) &&
        scr.every((d, k) => k === self || d.z < -0.4 ||
          d.x + d.r < b[0] || d.x - d.r > b[2] || d.y + d.r < b[1] || d.y - d.r > b[3]);
      const order = nodes.map((n, i) => i).filter((i) => nodes[i].label).sort((a, b) => scr[b].z - scr[a].z);
      for (const i of order) {
        const n = nodes[i], lb = n.label!, d = scr[i], hub = n.kind === "hub", gap = d.r + 6;
        const offsets: [number, number][] = hub
          ? [[-lb.w / 2, -gap - lb.h], [-lb.w / 2, gap]]
          : (d.x > w * 0.62 ? [-1, 1] : [1, -1]).map((side) => [side > 0 ? gap : -gap - lb.w, -lb.h / 2]);
        const minZ = hub ? -0.6 : -0.1;
        let target = 0, off = offsets[0];
        if (d.z > minZ) {
          for (const o of offsets) {
            const box: [number, number, number, number] = [d.x + o[0] - 2, d.y + o[1] - 2, d.x + o[0] + lb.w + 2, d.y + o[1] + lb.h + 2];
            if (!fits(box, i)) continue;
            placed.push(box);
            off = o;
            const edge = Math.min(box[0], box[1], w - box[2], h - box[3]);
            target = smooth(minZ, 0.8, d.z) * smooth(4, EDGE, edge);
            break;
          }
        }
        // Glide between sides while visible; jump while hidden so labels never sweep across the graph.
        const k = still || lb.alpha < 0.05 ? 1 : 0.18;
        lb.dx += (off[0] - lb.dx) * k;
        lb.dy += (off[1] - lb.dy) * k;
        lb.alpha = still ? target : lb.alpha + (target - lb.alpha) * 0.1;
        const st = lb.el.style;
        st.opacity = Math.max(lb.alpha, lb.flash * (target > 0 ? 1 : 0)).toFixed(3);
        st.transform = `translate3d(${(d.x + lb.dx).toFixed(1)}px, ${(d.y + lb.dy).toFixed(1)}px, 0)`;
        lb.el.classList.toggle("on", lb.flash > 0.3);
        lb.flash = 0;
      }
    };

    let raf = 0, visible = true;
    const t0 = performance.now();
    const loop = (now: number) => {
      frame((now - t0) / 1000);
      raf = visible ? requestAnimationFrame(loop) : 0;
    };
    const io = new IntersectionObserver(([e]) => {
      visible = e.isIntersecting;
      if (visible && !raf && !still) raf = requestAnimationFrame(loop);
    });
    if (still) frame(0);
    else {
      io.observe(host);
      raf = requestAnimationFrame(loop);
    }

    return () => {
      cancelAnimationFrame(raf);
      io.disconnect();
      ro.disconnect();
      window.removeEventListener("pointermove", onMove);
      [edgeLines, linkLines].forEach((l) => { l.geometry.dispose(); (l.material as THREE.Material).dispose(); });
      dotGeo.dispose();
      dotMat.dispose();
      (pulse.material as THREE.Material).dispose();
      dots.dispose();
      renderer.dispose();
      host.replaceChildren();
    };
  }, []);

  return <div ref={ref} className="kg" aria-hidden="true" />;
}
