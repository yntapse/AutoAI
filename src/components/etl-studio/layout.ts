/**
 * Automatic layout for the pipeline canvas. Pure functions: they only compute
 * positions. They never touch a node's settings, connections or any result.
 *
 * Layout rules (left to right, like the data flows):
 *   column 0  Source steps (tables)
 *   column n  a step sits one column right of its right-most input
 * Inside a column, steps are ordered so that steps feeding the same later
 * step sit next to each other, which keeps connection lines from crossing.
 */
import type { PipelineEdge, PipelineNode } from "./pipelineModel";

export interface LayoutOptions {
  nodeW: number;
  nodeH: number;
  gapX?: number;
  gapY?: number;
  originX?: number;
  originY?: number;
}

export interface Position {
  x: number;
  y: number;
}

/** Column of each node: 0 for nodes without inputs, otherwise 1 + the deepest input. */
export function columnsOf(nodes: readonly PipelineNode[], edges: readonly PipelineEdge[]): Record<string, number> {
  const ids = new Set(nodes.map((n) => n.id));
  const inputs: Record<string, string[]> = {};
  for (const e of edges) {
    if (!ids.has(e.source) || !ids.has(e.target)) continue;
    (inputs[e.target] ??= []).push(e.source);
  }
  const memo: Record<string, number> = {};
  const visiting = new Set<string>();
  const depth = (id: string): number => {
    if (memo[id] !== undefined) return memo[id];
    if (visiting.has(id)) return 0; // a loop cannot be laid out; the server rejects it anyway
    visiting.add(id);
    const d = (inputs[id] ?? []).reduce((m, s) => Math.max(m, depth(s) + 1), 0);
    visiting.delete(id);
    memo[id] = d;
    return d;
  };
  for (const n of nodes) depth(n.id);
  return memo;
}

export function layoutNodes(
  nodes: readonly PipelineNode[],
  edges: readonly PipelineEdge[],
  opts: LayoutOptions
): Record<string, Position> {
  const gapX = opts.gapX ?? 80;
  const gapY = opts.gapY ?? 44;
  const x0 = opts.originX ?? 40;
  const y0 = opts.originY ?? 40;
  const stepX = opts.nodeW + gapX;
  const stepY = opts.nodeH + gapY;

  const ids = new Set(nodes.map((n) => n.id));
  const col = columnsOf(nodes, edges);
  const maxCol = nodes.reduce((m, n) => Math.max(m, col[n.id] ?? 0), 0);
  const inputsOf: Record<string, string[]> = {};
  const outputsOf: Record<string, string[]> = {};
  const portOf: Record<string, number> = {}; // 'source>target' -> small offset so a left input sits above a right input
  for (const e of edges) {
    if (!ids.has(e.source) || !ids.has(e.target)) continue;
    (inputsOf[e.target] ??= []).push(e.source);
    (outputsOf[e.source] ??= []).push(e.target);
    portOf[`${e.source}>${e.target}`] = e.port === "left" ? -0.25 : e.port === "right" ? 0.25 : 0;
  }

  // Start from the order the nodes were created in (stable and predictable).
  const columns: string[][] = Array.from({ length: maxCol + 1 }, () => []);
  for (const n of nodes) columns[col[n.id] ?? 0].push(n.id);

  const order: Record<string, number> = {};
  const reindex = () => columns.forEach((c) => c.forEach((id, i) => (order[id] = i)));
  reindex();

  const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
  const sortBy = (c: string[], key: (id: string) => number | null) => {
    const original = new Map(c.map((id, i) => [id, i]));
    // Nodes with no neighbour on the reference side keep their place.
    c.sort((a, b) => {
      const ka = key(a) ?? original.get(a)!;
      const kb = key(b) ?? original.get(b)!;
      return ka - kb || original.get(a)! - original.get(b)!;
    });
  };

  for (let sweep = 0; sweep < 3; sweep += 1) {
    for (let c = 1; c <= maxCol; c += 1) {
      sortBy(columns[c], (id) => (inputsOf[id]?.length ? mean(inputsOf[id].map((s) => order[s])) : null));
      reindex();
    }
    for (let c = maxCol - 1; c >= 0; c -= 1) {
      sortBy(columns[c], (id) => (outputsOf[id]?.length ? mean(outputsOf[id].map((t) => order[t] + (portOf[`${id}>${t}`] ?? 0))) : null));
      reindex();
    }
  }

  // Vertical positions: column 0 is evenly spaced; later columns aim at the middle of their inputs.
  const y: Record<string, number> = {};
  columns[0]?.forEach((id, i) => (y[id] = y0 + i * stepY));
  for (let c = 1; c <= maxCol; c += 1) {
    const wanted = columns[c].map((id, i) => ({
      id,
      want: inputsOf[id]?.length ? mean(inputsOf[id].map((s) => y[s] ?? y0)) : y0 + i * stepY,
    }));
    wanted.sort((a, b) => a.want - b.want || order[a.id] - order[b.id]);
    let floor = y0;
    for (const w of wanted) {
      y[w.id] = Math.max(w.want, floor);
      floor = y[w.id] + stepY;
    }
  }

  const out: Record<string, Position> = {};
  for (const n of nodes) out[n.id] = { x: x0 + (col[n.id] ?? 0) * stepX, y: Math.round(y[n.id] ?? y0) };
  return out;
}

/** Count pairs of straight connection lines that cross, to compare layouts. */
export function crossings(nodes: readonly PipelineNode[], edges: readonly PipelineEdge[], pos: Record<string, Position>): number {
  const lines = edges
    .filter((e) => pos[e.source] && pos[e.target])
    .map((e) => ({ a: pos[e.source], b: pos[e.target], s: e.source, t: e.target }));
  let n = 0;
  for (let i = 0; i < lines.length; i += 1) {
    for (let j = i + 1; j < lines.length; j += 1) {
      const p = lines[i];
      const q = lines[j];
      if (p.s === q.s || p.t === q.t) continue; // lines that share an end do not "cross"
      const o = (ax: number, ay: number, bx: number, by: number, cx: number, cy: number) => Math.sign((bx - ax) * (cy - ay) - (by - ay) * (cx - ax));
      const o1 = o(p.a.x, p.a.y, p.b.x, p.b.y, q.a.x, q.a.y);
      const o2 = o(p.a.x, p.a.y, p.b.x, p.b.y, q.b.x, q.b.y);
      const o3 = o(q.a.x, q.a.y, q.b.x, q.b.y, p.a.x, p.a.y);
      const o4 = o(q.a.x, q.a.y, q.b.x, q.b.y, p.b.x, p.b.y);
      if (o1 !== o2 && o3 !== o4 && o1 !== 0 && o2 !== 0 && o3 !== 0 && o4 !== 0) n += 1;
    }
  }
  return n;
}
