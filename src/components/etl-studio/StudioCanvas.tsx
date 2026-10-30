"use client";

/**
 * The pipeline canvas: draggable nodes, directional connection lines, pan and
 * zoom. It is a picture of the pipeline. Moving, connecting or removing things
 * here changes only that picture; it never contacts a database.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fitViewport, zoomAt, type Viewport } from "@/components/catalog/relationshipGraph";
import {
  NODE_META,
  STATUS_LABELS,
  inputPorts,
  type DisplayStatus,
  type PipelineEdge,
  type PipelineNode,
} from "./pipelineModel";

export const NODE_W = 232;
export const NODE_H = 124;

export interface NodeView {
  title: string;
  subtitle: string;
  detail?: string;
  /** Which data source the step reads from, and whether it is the connected (main) one. */
  badge?: string;
  badgeIsMain?: boolean;
  status: DisplayStatus;
  outdated: boolean;
}

interface Props {
  nodes: PipelineNode[];
  edges: PipelineEdge[];
  views: Record<string, NodeView | undefined>;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onMove: (id: string, x: number, y: number) => void;
  /** Returns a message when the connection is refused, otherwise null. */
  onConnect: (source: string, target: string, port: string) => string | null;
  onRemoveEdge: (edge: PipelineEdge) => void;
  /** Arrange the steps left to right (positions only). */
  onTidy?: () => void;
  /** Present only while the last tidy-up can be undone. */
  onUndoTidy?: () => void;
  /** Changes whenever the view should be fitted to the steps (after a tidy-up). */
  fitToken?: number;
}

const STATUS_STYLE: Record<DisplayStatus, string> = {
  not_configured: "border-slate-500/30 bg-slate-500/10 text-slate-300",
  invalid: "border-rose-500/30 bg-rose-500/10 text-rose-300",
  blocked: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  ready: "border-cyan-500/30 bg-cyan-500/10 text-cyan-300",
  running: "border-violet-500/30 bg-violet-500/10 text-violet-300 animate-pulse",
  successful: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  failed: "border-rose-500/30 bg-rose-500/10 text-rose-300",
};

const TYPE_DOT: Record<string, string> = {
  source: "bg-cyan-400",
  join: "bg-violet-400",
  output: "bg-emerald-400",
};

function inPortY(index: number, count: number): number {
  return (NODE_H * (index + 1)) / (count + 1);
}

function curve(x1: number, y1: number, x2: number, y2: number): string {
  const dx = Math.max(48, Math.abs(x2 - x1) / 2);
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

export default function StudioCanvas({
  nodes,
  edges,
  views,
  selectedId,
  onSelect,
  onMove,
  onConnect,
  onRemoveEdge,
  onTidy,
  onUndoTidy,
  fitToken,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<Viewport>({ x: 40, y: 40, k: 1 });
  const [connecting, setConnecting] = useState<{ source: string; x: number; y: number } | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const gesture = useRef<
    | { kind: "pan"; startX: number; startY: number; vx: number; vy: number }
    | { kind: "node"; id: string; startX: number; startY: number; nx: number; ny: number; moved: boolean }
    | null
  >(null);

  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);

  // Zoom with the wheel around the pointer. Needs a non-passive listener.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = el.getBoundingClientRect();
      setView((v) => zoomAt(v, event.deltaY < 0 ? 1.1 : 1 / 1.1, event.clientX - rect.left, event.clientY - rect.top));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const toWorld = useCallback(
    (clientX: number, clientY: number) => {
      const rect = wrapRef.current?.getBoundingClientRect();
      return {
        x: (clientX - (rect?.left ?? 0) - view.x) / view.k,
        y: (clientY - (rect?.top ?? 0) - view.y) / view.k,
      };
    },
    [view]
  );

  // While a connection is being dragged, follow the pointer and drop on a port.
  const connectingFrom = connecting?.source ?? null;
  useEffect(() => {
    if (!connectingFrom) return undefined;
    const move = (event: PointerEvent) => {
      const p = toWorld(event.clientX, event.clientY);
      setConnecting({ source: connectingFrom, x: p.x, y: p.y });
    };
    const up = (event: PointerEvent) => {
      const target = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>("[data-input-port]");
      setConnecting(null);
      if (target?.dataset.node && target.dataset.port) {
        setRefusal(onConnect(connectingFrom, target.dataset.node, target.dataset.port));
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [connectingFrom, onConnect, toWorld]);

  function onBackgroundDown(event: React.PointerEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget && !(event.target as HTMLElement).dataset.canvasBg) return;
    gesture.current = { kind: "pan", startX: event.clientX, startY: event.clientY, vx: view.x, vy: view.y };
    event.currentTarget.setPointerCapture(event.pointerId);
    setRefusal(null);
  }

  function onNodeDown(event: React.PointerEvent<HTMLDivElement>, node: PipelineNode) {
    event.stopPropagation();
    gesture.current = { kind: "node", id: node.id, startX: event.clientX, startY: event.clientY, nx: node.x, ny: node.y, moved: false };
    wrapRef.current?.setPointerCapture(event.pointerId);
    onSelect(node.id);
    setRefusal(null);
  }

  function onPointerMove(event: React.PointerEvent<HTMLDivElement>) {
    const g = gesture.current;
    if (!g) return;
    if (g.kind === "pan") {
      setView((v) => ({ ...v, x: g.vx + event.clientX - g.startX, y: g.vy + event.clientY - g.startY }));
    } else {
      const dx = (event.clientX - g.startX) / view.k;
      const dy = (event.clientY - g.startY) / view.k;
      if (Math.abs(dx) + Math.abs(dy) > 1) g.moved = true;
      onMove(g.id, g.nx + dx, g.ny + dy);
    }
  }

  function onPointerUp(event: React.PointerEvent<HTMLDivElement>) {
    const g = gesture.current;
    gesture.current = null;
    if (wrapRef.current?.hasPointerCapture(event.pointerId)) wrapRef.current.releasePointerCapture(event.pointerId);
    if (g?.kind === "pan" && Math.abs(event.clientX - g.startX) + Math.abs(event.clientY - g.startY) < 3) onSelect(null);
  }

  function startConnect(event: React.PointerEvent<HTMLElement>, node: PipelineNode) {
    event.stopPropagation();
    event.preventDefault();
    const p = toWorld(event.clientX, event.clientY);
    setRefusal(null);
    setConnecting({ source: node.id, x: p.x, y: p.y });
  }

  function fit() {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect || nodes.length === 0) {
      setView({ x: 40, y: 40, k: 1 });
      return;
    }
    setView(fitViewport(nodes.map((n) => ({ x: n.x, y: n.y, w: NODE_W, h: NODE_H })), rect.width, rect.height));
  }

  // After a tidy-up (or its undo) bring every step into view.
  useEffect(() => {
    if (fitToken) fit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitToken]);

  const zoomBy = (factor: number) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    setView((v) => zoomAt(v, factor, (rect?.width ?? 0) / 2, (rect?.height ?? 0) / 2));
  };

  return (
    <div className="relative h-full min-h-[360px] overflow-hidden rounded-2xl border border-white/[0.06] bg-[#0b1018]">
      <div
        ref={wrapRef}
        data-testid="studio-canvas"
        className="absolute inset-0 touch-none select-none"
        style={{
          backgroundImage: "radial-gradient(rgba(148,163,184,0.16) 1px, transparent 1px)",
          backgroundSize: `${24 * view.k}px ${24 * view.k}px`,
          backgroundPosition: `${view.x}px ${view.y}px`,
        }}
        onPointerDown={onBackgroundDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      >
        <div
          data-canvas-bg="true"
          className="absolute left-0 top-0"
          style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})`, transformOrigin: "0 0" }}
        >
          <svg className="pointer-events-none absolute left-0 top-0 overflow-visible" width={1} height={1}>
            <defs>
              <marker id="studio-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b" />
              </marker>
            </defs>
            {edges.map((edge) => {
              const from = byId.get(edge.source);
              const to = byId.get(edge.target);
              if (!from || !to) return null;
              const ports = inputPorts(to.type);
              const idx = Math.max(0, ports.indexOf(edge.port));
              const x1 = from.x + NODE_W;
              const y1 = from.y + NODE_H / 2;
              const x2 = to.x;
              const y2 = to.y + inPortY(idx, ports.length);
              const mx = (x1 + x2) / 2;
              const my = (y1 + y2) / 2;
              return (
                <g key={`${edge.source}>${edge.target}:${edge.port}`} data-testid="studio-edge">
                  <path d={curve(x1, y1, x2, y2)} fill="none" stroke="#64748b" strokeWidth={1.8} markerEnd="url(#studio-arrow)" />
                  <g
                    className="pointer-events-auto cursor-pointer"
                    role="button"
                    aria-label={`Remove connection from ${edge.source} to ${edge.target}`}
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={() => onRemoveEdge(edge)}
                  >
                    <circle cx={mx} cy={my} r={9} fill="#0b1018" stroke="#475569" />
                    <text x={mx} y={my + 4} textAnchor="middle" fontSize={12} fill="#cbd5e1">×</text>
                  </g>
                </g>
              );
            })}
            {connecting && byId.get(connecting.source) && (
              <path
                d={curve(
                  (byId.get(connecting.source) as PipelineNode).x + NODE_W,
                  (byId.get(connecting.source) as PipelineNode).y + NODE_H / 2,
                  connecting.x,
                  connecting.y
                )}
                fill="none"
                stroke="#22d3ee"
                strokeDasharray="5 4"
                strokeWidth={1.8}
              />
            )}
          </svg>

          {nodes.map((node) => {
            const nv = views[node.id];
            const meta = NODE_META[node.type];
            const ports = inputPorts(node.type);
            const selected = selectedId === node.id;
            return (
              <div
                key={node.id}
                data-testid={`studio-node-${node.id}`}
                data-node-type={node.type}
                data-status={nv?.status ?? "not_configured"}
                className={`absolute rounded-xl border bg-[#111826] shadow-lg ${
                  selected ? "border-cyan-400/70 ring-1 ring-cyan-400/40" : "border-white/[0.1]"
                }`}
                style={{ left: node.x, top: node.y, width: NODE_W, height: NODE_H }}
              >
                <div
                  className="flex cursor-grab items-center gap-2 rounded-t-xl border-b border-white/[0.06] px-3 py-1.5 active:cursor-grabbing"
                  onPointerDown={(e) => onNodeDown(e, node)}
                >
                  <span className={`h-2 w-2 shrink-0 rounded-full ${TYPE_DOT[node.type] ?? "bg-amber-400"}`} />
                  <span className="truncate text-[11px] font-semibold uppercase tracking-wide text-slate-300">{meta.label}</span>
                  <span className="ml-auto truncate text-[10px] text-slate-600">{node.id}</span>
                </div>
                <div className="px-3 py-1.5" onPointerDown={(e) => onNodeDown(e, node)}>
                  <div className="truncate text-[12.5px] font-medium text-white">{nv?.title ?? meta.label}</div>
                  <div className="truncate text-[11px] text-slate-400">{nv?.subtitle}</div>
                  {nv?.detail && <div className="truncate text-[10.5px] text-slate-500">{nv.detail}</div>}
                  {nv?.badge && (
                    <div
                      className={`mt-0.5 truncate text-[10px] ${nv.badgeIsMain ? "text-slate-500" : "text-violet-300"}`}
                      title={nv.badgeIsMain ? "Data source (connected)" : "A different data source: it can be checked here but not previewed"}
                      data-testid="source-badge"
                      data-main={nv.badgeIsMain ? "true" : "false"}
                    >
                      {nv.badge}
                    </div>
                  )}
                </div>
                <div className="absolute -bottom-2.5 left-3 flex gap-1.5">
                  <span className={`rounded-full border px-2 py-[1px] text-[10px] ${STATUS_STYLE[nv?.status ?? "not_configured"]}`}>
                    {STATUS_LABELS[nv?.status ?? "not_configured"]}
                  </span>
                  {nv?.outdated && (
                    <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-[1px] text-[10px] text-amber-300">
                      Out of date
                    </span>
                  )}
                </div>

                {ports.map((port, i) => (
                  <div
                    key={port}
                    data-input-port="true"
                    data-node={node.id}
                    data-port={port}
                    title={ports.length > 1 ? `${port} input` : "input"}
                    className="absolute -left-2 h-4 w-4 rounded-full border-2 border-slate-500 bg-[#0b1018] after:absolute after:-inset-2 after:content-[''] hover:border-cyan-400"
                    style={{ top: inPortY(i, ports.length) - 8 }}
                  >
                    {ports.length > 1 && (
                      <span className="pointer-events-none absolute -left-9 top-0 w-8 text-right text-[9px] leading-4 text-slate-500">{port}</span>
                    )}
                  </div>
                ))}
                {node.type !== "output" && (
                  <div
                    data-output-port="true"
                    data-node={node.id}
                    title="Drag to an input to connect"
                    className="absolute -right-2 h-4 w-4 cursor-crosshair rounded-full border-2 border-cyan-500 bg-[#0b1018] after:absolute after:-inset-2 after:content-[''] hover:bg-cyan-500/30"
                    style={{ top: NODE_H / 2 - 8 }}
                    onPointerDown={(e) => startConnect(e, node)}
                  />
                )}
              </div>
            );
          })}
        </div>
      </div>

      {nodes.length === 0 && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <p className="max-w-xs text-center text-[13px] text-slate-500">
            Add a table from the list on the left to start. Then add a join or a transformation and connect them by dragging from the
            circle on the right of a node to the circle on the left of another.
          </p>
        </div>
      )}

      <div className="absolute right-3 top-3 flex items-center gap-1 rounded-lg border border-white/[0.08] bg-[#0b1018]/90 p-1 text-[12px] text-slate-300">
        <button className="rounded px-2 py-0.5 hover:bg-white/[0.08]" onClick={() => zoomBy(1.2)} aria-label="Zoom in">+</button>
        <button className="rounded px-2 py-0.5 hover:bg-white/[0.08]" onClick={() => zoomBy(1 / 1.2)} aria-label="Zoom out">−</button>
        <button className="rounded px-2 py-0.5 hover:bg-white/[0.08]" onClick={fit}>Fit</button>
        {onTidy && (
          <button className="rounded px-2 py-0.5 hover:bg-white/[0.08]" data-testid="tidy-up" onClick={onTidy} title="Arrange the steps left to right. Only positions change.">
            Tidy up
          </button>
        )}
        {onUndoTidy && (
          <button className="rounded px-2 py-0.5 hover:bg-white/[0.08]" data-testid="undo-tidy" onClick={onUndoTidy}>
            Undo tidy up
          </button>
        )}
        <span className="px-1 text-slate-500">{Math.round(view.k * 100)}%</span>
      </div>

      {refusal && (
        <div role="alert" className="absolute bottom-3 left-3 right-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-200">
          Connection not made: {refusal}
        </div>
      )}
    </div>
  );
}
