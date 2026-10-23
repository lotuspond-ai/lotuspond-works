import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from 'react';
import type { Edge, NodeKind, PackageNode } from '../../lib/packageContract.js';
import {
  GRID,
  KIND_LABELS,
  NODE_HEIGHT,
  NODE_WIDTH,
  NODE_KINDS_IN_ORDER,
  canvasSize,
  sameEdge,
  type Draft,
  type Position,
} from '../../lib/studio.js';
import { Icon, type IconName } from '../console/icons.js';

export type Selection = { kind: 'node'; id: string } | { kind: 'edge'; edge: Edge } | null;

export const KIND_ICONS: Record<NodeKind, IconName> = {
  start: 'start',
  end: 'end',
  agent: 'agent',
  connector: 'integrations',
  human_review: 'approvals',
};

const DRAG_THRESHOLD = 4;
const PORT_RADIUS = 7;
const ZOOM_LEVELS = [0.5, 0.67, 0.8, 1, 1.25, 1.5];
const SCROLL_PADDING = 24;

type Wire = { source: string; x: number; y: number };
type Ghost = { kind: NodeKind; x: number; y: number };
type Overflow = { left: boolean; right: boolean; top: boolean; bottom: boolean };

// Where an edge leaves one node and enters the next, in canvas coordinates: the outer edge of
// each port, so the arrowhead stays visible beside the port.
const outPort = (p: Position) => ({
  x: p.x + NODE_WIDTH + PORT_RADIUS,
  y: p.y + NODE_HEIGHT / 2,
});
const inPort = (p: Position) => ({ x: p.x - PORT_RADIUS, y: p.y + NODE_HEIGHT / 2 });
function edgePath(from: Position, to: Position) {
  const bend = Math.max(40, Math.abs(to.x - from.x) / 2);
  return `M${from.x} ${from.y} C${from.x + bend} ${from.y}, ${to.x - bend} ${to.y}, ${to.x} ${to.y}`;
}

export function StudioCanvas({
  draft,
  selected,
  flagged,
  connecting,
  onSelect,
  onMove,
  onConnect,
  onStartConnect,
  onAdd,
  onDelete,
}: {
  draft: Draft;
  selected: Selection;
  // Node identifiers the admission check marked, with the messages for each.
  flagged: Map<string, string[]>;
  // The node a connection is being made from, by keyboard or a click on its port.
  connecting: string | null;
  onSelect: (selection: Selection) => void;
  onMove: (id: string, position: Position) => void;
  onConnect: (source: string, target: string) => void;
  onStartConnect: (id: string | null) => void;
  onAdd: (kind: NodeKind, position?: Position) => void;
  onDelete: (selection: Selection) => void;
}) {
  const surface = useRef<HTMLDivElement>(null);
  const [wire, setWire] = useState<Wire | null>(null);
  const [ghost, setGhost] = useState<Ghost | null>(null);
  const [dragging, setDragging] = useState('');
  const [scale, setScale] = useState(1);
  const [overflow, setOverflow] = useState<Overflow>({
    left: false,
    right: false,
    top: false,
    bottom: false,
  });
  const gesture = useRef<{
    id: string;
    startX: number;
    startY: number;
    originX: number;
    originY: number;
    moved: boolean;
  } | null>(null);
  const paletteGesture = useRef<{
    kind: NodeKind;
    startX: number;
    startY: number;
    moved: boolean;
  } | null>(null);
  // A drop is followed by the button's own click; that click must not add a second node.
  const suppressClick = useRef(false);
  const size = canvasSize(draft.layout);
  const positions = draft.layout;
  const nodeById = new Map(draft.package.nodes.map((node) => [node.id, node]));

  // Pointer position translated into the canvas's own coordinates, at the current zoom.
  function localPoint(clientX: number, clientY: number): Position {
    const element = surface.current;
    if (!element) return { x: 0, y: 0 };
    const rect = element.getBoundingClientRect();
    return {
      x: (clientX - rect.left + element.scrollLeft) / scale,
      y: (clientY - rect.top + element.scrollTop) / scale,
    };
  }
  function nodeAt(clientX: number, clientY: number): string | null {
    const hit = document.elementFromPoint(clientX, clientY)?.closest('[data-node-id]');
    return hit instanceof HTMLElement ? (hit.dataset.nodeId ?? null) : null;
  }

  // Shades along the edges where more of the canvas lies beyond the view.
  function measureOverflow() {
    const element = surface.current;
    if (!element) return;
    const next = {
      left: element.scrollLeft > 1,
      right: element.scrollLeft + element.clientWidth < element.scrollWidth - 1,
      top: element.scrollTop > 1,
      bottom: element.scrollTop + element.clientHeight < element.scrollHeight - 1,
    };
    setOverflow((current) =>
      current.left === next.left &&
      current.right === next.right &&
      current.top === next.top &&
      current.bottom === next.bottom
        ? current
        : next,
    );
  }
  useLayoutEffect(() => {
    measureOverflow();
    const element = surface.current;
    if (!element) return;
    const observer = new ResizeObserver(measureOverflow);
    observer.observe(element);
    return () => observer.disconnect();
  }, [size.width, size.height, scale]);

  // The selected node is brought into view, so choosing it from a problem keeps it in sight.
  useEffect(() => {
    if (selected?.kind !== 'node') return;
    const element = surface.current;
    const position = positions[selected.id];
    if (!element || !position) return;
    const left = (position.x - PORT_RADIUS) * scale;
    const right = (position.x + NODE_WIDTH + PORT_RADIUS) * scale;
    const top = position.y * scale;
    const bottom = (position.y + NODE_HEIGHT) * scale;
    if (left < element.scrollLeft) element.scrollLeft = left - SCROLL_PADDING;
    else if (right > element.scrollLeft + element.clientWidth)
      element.scrollLeft = right - element.clientWidth + SCROLL_PADDING;
    if (top < element.scrollTop) element.scrollTop = top - SCROLL_PADDING;
    else if (bottom > element.scrollTop + element.clientHeight)
      element.scrollTop = bottom - element.clientHeight + SCROLL_PADDING;
    // Only a change of selection scrolls; moving the node must not fight the pointer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  function zoomTo(next: number) {
    const element = surface.current;
    if (element) {
      const ratio = next / scale;
      element.scrollLeft *= ratio;
      element.scrollTop *= ratio;
    }
    setScale(next);
  }
  function zoomStep(direction: 1 | -1) {
    const next =
      direction > 0
        ? ZOOM_LEVELS.find((level) => level > scale + 0.001)
        : [...ZOOM_LEVELS].reverse().find((level) => level < scale - 0.001);
    if (next) zoomTo(next);
  }
  function zoomToFit() {
    const element = surface.current;
    if (!element) return;
    const fit = Math.min(
      (element.clientWidth - 2) / size.width,
      (element.clientHeight - 2) / size.height,
    );
    zoomTo(Math.min(1, Math.max(ZOOM_LEVELS[0], Math.floor(fit * 100) / 100)));
  }

  // Moving a node: a press that travels further than the threshold drags it; a shorter
  // press selects it. Pointer capture keeps the gesture even when the pointer leaves the card.
  function nodePointerDown(event: PointerEvent<HTMLDivElement>, id: string) {
    if (event.button !== 0) return;
    const position = positions[id];
    if (!position) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = {
      id,
      startX: event.clientX,
      startY: event.clientY,
      originX: position.x,
      originY: position.y,
      moved: false,
    };
  }
  function nodePointerMove(event: PointerEvent<HTMLDivElement>) {
    const current = gesture.current;
    if (!current) return;
    const dx = event.clientX - current.startX;
    const dy = event.clientY - current.startY;
    if (!current.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
    if (!current.moved) {
      current.moved = true;
      setDragging(current.id);
    }
    onMove(current.id, { x: current.originX + dx / scale, y: current.originY + dy / scale });
  }
  function nodePointerUp(event: PointerEvent<HTMLDivElement>, id: string) {
    const current = gesture.current;
    gesture.current = null;
    setDragging('');
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    if (current?.moved) return;
    if (connecting && connecting !== id) {
      onConnect(connecting, id);
      onStartConnect(null);
      return;
    }
    onSelect({ kind: 'node', id });
  }

  // Connecting: dragging from a node's port draws a wire that lands on another node; a
  // plain press on the port enters connect mode, where the next node chosen is the target.
  function portPointerDown(event: PointerEvent<HTMLButtonElement>, id: string) {
    if (event.button !== 0) return;
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    const point = localPoint(event.clientX, event.clientY);
    setWire({ source: id, x: point.x, y: point.y });
    gesture.current = {
      id,
      startX: event.clientX,
      startY: event.clientY,
      originX: 0,
      originY: 0,
      moved: false,
    };
  }
  function portPointerMove(event: PointerEvent<HTMLButtonElement>) {
    const current = gesture.current;
    if (!current || !wire) return;
    if (
      Math.hypot(event.clientX - current.startX, event.clientY - current.startY) >= DRAG_THRESHOLD
    )
      current.moved = true;
    const point = localPoint(event.clientX, event.clientY);
    setWire({ source: current.id, x: point.x, y: point.y });
  }
  function portPointerUp(event: PointerEvent<HTMLButtonElement>, id: string) {
    const current = gesture.current;
    gesture.current = null;
    setWire(null);
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    if (current?.moved) {
      const target = nodeAt(event.clientX, event.clientY);
      if (target && target !== id) onConnect(id, target);
      return;
    }
    onStartConnect(connecting === id ? null : id);
  }

  // Adding from the palette: drag a kind onto the canvas to place it there, or press the
  // button (pointer or keyboard) to add it at a free position.
  function palettePointerDown(event: PointerEvent<HTMLButtonElement>, kind: NodeKind) {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    suppressClick.current = false;
    paletteGesture.current = { kind, startX: event.clientX, startY: event.clientY, moved: false };
  }
  function palettePointerMove(event: PointerEvent<HTMLButtonElement>) {
    const current = paletteGesture.current;
    if (!current) return;
    if (
      !current.moved &&
      Math.hypot(event.clientX - current.startX, event.clientY - current.startY) < DRAG_THRESHOLD
    )
      return;
    current.moved = true;
    setGhost({ kind: current.kind, x: event.clientX, y: event.clientY });
  }
  function palettePointerUp(event: PointerEvent<HTMLButtonElement>) {
    const current = paletteGesture.current;
    paletteGesture.current = null;
    setGhost(null);
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    if (!current?.moved) return;
    suppressClick.current = true;
    const rect = surface.current?.getBoundingClientRect();
    if (
      rect &&
      event.clientX >= rect.left &&
      event.clientX <= rect.right &&
      event.clientY >= rect.top &&
      event.clientY <= rect.bottom
    ) {
      const point = localPoint(event.clientX, event.clientY);
      onAdd(current.kind, { x: point.x - NODE_WIDTH / 2, y: point.y - NODE_HEIGHT / 2 });
    }
  }
  function paletteClick(kind: NodeKind) {
    if (suppressClick.current) {
      suppressClick.current = false;
      return;
    }
    onAdd(kind);
  }

  function nodeKeyDown(event: KeyboardEvent<HTMLDivElement>, id: string) {
    const position = positions[id];
    if (!position) return;
    const step = event.shiftKey ? GRID * 5 : GRID;
    const moves: Record<string, Position> = {
      ArrowLeft: { x: position.x - step, y: position.y },
      ArrowRight: { x: position.x + step, y: position.y },
      ArrowUp: { x: position.x, y: position.y - step },
      ArrowDown: { x: position.x, y: position.y + step },
    };
    if (moves[event.key]) {
      event.preventDefault();
      onMove(id, moves[event.key]);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (connecting && connecting !== id) {
        onConnect(connecting, id);
        onStartConnect(null);
      } else onSelect({ kind: 'node', id });
    } else if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault();
      onDelete({ kind: 'node', id });
    } else if (event.key === 'Escape' && connecting) {
      event.preventDefault();
      onStartConnect(null);
    }
  }
  useEffect(() => {
    if (!connecting) return;
    const cancel = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') onStartConnect(null);
    };
    document.addEventListener('keydown', cancel);
    return () => document.removeEventListener('keydown', cancel);
  }, [connecting, onStartConnect]);

  const isSelectedEdge = (edge: Edge) => selected?.kind === 'edge' && sameEdge(selected.edge, edge);
  const wireFrom = wire ? positions[wire.source] : undefined;

  return (
    <div className="studio-canvas">
      <div className="studio-palette" role="toolbar" aria-label="Add a node">
        <span className="studio-palette-label">Add</span>
        {NODE_KINDS_IN_ORDER.map((kind) => (
          <button
            key={kind}
            type="button"
            className="studio-palette-item"
            title={`Drag onto the canvas, or press to add ${KIND_LABELS[kind].toLowerCase()}`}
            onPointerDown={(event) => palettePointerDown(event, kind)}
            onPointerMove={palettePointerMove}
            onPointerUp={palettePointerUp}
            onPointerCancel={() => {
              paletteGesture.current = null;
              setGhost(null);
            }}
            onClick={() => paletteClick(kind)}
          >
            <Icon name={KIND_ICONS[kind]} />
            <span>{KIND_LABELS[kind]}</span>
          </button>
        ))}
        <span className="studio-palette-hint">Drag onto the canvas or press to add</span>
      </div>
      <div className="studio-frame">
        <div
          ref={surface}
          className={`studio-surface ${connecting ? 'studio-connecting' : ''}`}
          onScroll={measureOverflow}
          onPointerDown={(event) => {
            const target = event.target as Element;
            if (target.closest('[data-node-id], .studio-edge')) return;
            onSelect(null);
            if (connecting) onStartConnect(null);
          }}
        >
          <div
            className="studio-world-extent"
            style={{ width: size.width * scale, height: size.height * scale }}
          >
            <div
              className="studio-world"
              style={{ width: size.width, height: size.height, transform: `scale(${scale})` }}
            >
              <svg
                className="studio-edges"
                width={size.width}
                height={size.height}
                aria-hidden="true"
              >
                <defs>
                  {(['ink', 'accent', 'danger'] as const).map((tone) => (
                    <marker
                      key={tone}
                      id={`studio-arrow-${tone}`}
                      className={`studio-arrow studio-arrow-${tone}`}
                      viewBox="0 0 10 10"
                      refX="9"
                      refY="5"
                      markerWidth="7"
                      markerHeight="7"
                      orient="auto-start-reverse"
                    >
                      <path d="M1 1 L9 5 L1 9" />
                    </marker>
                  ))}
                </defs>
                {draft.package.edges.map((edge) => {
                  const from = positions[edge.source];
                  const to = positions[edge.target];
                  if (!from || !to) return null;
                  const d = edgePath(outPort(from), inPort(to));
                  const tone = isSelectedEdge(edge)
                    ? 'accent'
                    : flagged.has(edge.source) && flagged.has(edge.target)
                      ? 'danger'
                      : 'ink';
                  return (
                    <g
                      key={`${edge.source}>${edge.target}`}
                      className={`studio-edge studio-edge-${tone}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        onSelect({ kind: 'edge', edge });
                      }}
                    >
                      <path className="studio-edge-hit" d={d} />
                      <path
                        className="studio-edge-line"
                        d={d}
                        markerEnd={`url(#studio-arrow-${tone})`}
                      />
                    </g>
                  );
                })}
                {wire && wireFrom && (
                  <path
                    className="studio-edge-line studio-wire"
                    d={edgePath(outPort(wireFrom), { x: wire.x, y: wire.y })}
                  />
                )}
              </svg>
              {draft.package.nodes.map((node) => {
                const position = positions[node.id];
                if (!position) return null;
                const problems = flagged.get(node.id);
                const isSelected = selected?.kind === 'node' && selected.id === node.id;
                return (
                  <StudioNode
                    key={node.id}
                    node={node}
                    position={position}
                    selected={isSelected}
                    dragging={dragging === node.id}
                    problems={problems}
                    connectingFrom={connecting === node.id}
                    connectTarget={!!connecting && connecting !== node.id}
                    onPointerDown={(event) => nodePointerDown(event, node.id)}
                    onPointerMove={nodePointerMove}
                    onPointerUp={(event) => nodePointerUp(event, node.id)}
                    onKeyDown={(event) => nodeKeyDown(event, node.id)}
                    onPortPointerDown={(event) => portPointerDown(event, node.id)}
                    onPortPointerMove={portPointerMove}
                    onPortPointerUp={(event) => portPointerUp(event, node.id)}
                    onPortToggle={() => onStartConnect(connecting === node.id ? null : node.id)}
                  />
                );
              })}
            </div>
          </div>
        </div>
        {(['left', 'right', 'top', 'bottom'] as const).map(
          (side) =>
            overflow[side] && (
              <span key={side} className={`studio-shade studio-shade-${side}`} aria-hidden="true" />
            ),
        )}
      </div>
      <div className="studio-canvas-footer">
        <p className="studio-canvas-status" role="status">
          {connecting
            ? `Connecting from ${connecting}: choose the node it leads to, or press Escape.`
            : selected?.kind === 'node'
              ? `${KIND_LABELS[nodeById.get(selected.id)?.kind ?? 'start']} ${selected.id} selected. Arrow keys move it; Delete removes it.`
              : selected?.kind === 'edge'
                ? `Edge ${selected.edge.source} → ${selected.edge.target} selected.`
                : `${draft.package.nodes.length} nodes and ${draft.package.edges.length} edges.`}
        </p>
        <div className="studio-zoom" role="group" aria-label="Zoom">
          <button
            type="button"
            className="secondary"
            aria-label="Zoom out"
            disabled={scale <= ZOOM_LEVELS[0]}
            onClick={() => zoomStep(-1)}
          >
            −
          </button>
          <span className="studio-zoom-level" aria-live="polite">
            {Math.round(scale * 100)}%
          </span>
          <button
            type="button"
            className="secondary"
            aria-label="Zoom in"
            disabled={scale >= ZOOM_LEVELS[ZOOM_LEVELS.length - 1]}
            onClick={() => zoomStep(1)}
          >
            +
          </button>
          <button type="button" className="secondary" onClick={zoomToFit}>
            Fit
          </button>
        </div>
      </div>
      {ghost && (
        <div className="studio-ghost" style={{ left: ghost.x, top: ghost.y }} aria-hidden="true">
          <Icon name={KIND_ICONS[ghost.kind]} />
          {KIND_LABELS[ghost.kind]}
        </div>
      )}
    </div>
  );
}

// A node card and, beside it, the port a new edge is drawn from. The port is a sibling rather
// than a child so that no control sits inside the card's own button role.
function StudioNode({
  node,
  position,
  selected,
  dragging,
  problems,
  connectingFrom,
  connectTarget,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  onKeyDown,
  onPortPointerDown,
  onPortPointerMove,
  onPortPointerUp,
  onPortToggle,
}: {
  node: PackageNode;
  position: Position;
  selected: boolean;
  dragging: boolean;
  problems?: string[];
  connectingFrom: boolean;
  connectTarget: boolean;
  onPointerDown: (event: PointerEvent<HTMLDivElement>) => void;
  onPointerMove: (event: PointerEvent<HTMLDivElement>) => void;
  onPointerUp: (event: PointerEvent<HTMLDivElement>) => void;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void;
  onPortPointerDown: (event: PointerEvent<HTMLButtonElement>) => void;
  onPortPointerMove: (event: PointerEvent<HTMLButtonElement>) => void;
  onPortPointerUp: (event: PointerEvent<HTMLButtonElement>) => void;
  onPortToggle: () => void;
}) {
  const detail =
    node.kind === 'agent'
      ? node.model_ref || 'No model chosen'
      : node.kind === 'connector'
        ? [node.connector_ref, node.operation].filter(Boolean).join(' · ') || 'No connector chosen'
        : KIND_LABELS[node.kind];
  const describedBy = problems?.length ? `studio-problems-${node.id}` : undefined;
  const terminal = node.kind === 'start' || node.kind === 'end';
  return (
    <>
      <div
        className={[
          'studio-node',
          `studio-node-${node.kind}`,
          selected ? 'studio-node-selected' : '',
          dragging ? 'studio-node-dragging' : '',
          problems?.length ? 'studio-node-flagged' : '',
          connectTarget ? 'studio-node-target' : '',
        ].join(' ')}
        style={{ transform: `translate(${position.x}px, ${position.y}px)` }}
        data-node-id={node.id}
        role="button"
        tabIndex={0}
        aria-pressed={selected}
        aria-label={`${KIND_LABELS[node.kind]} ${node.id}${problems?.length ? ', needs attention' : ''}`}
        aria-describedby={describedBy}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onKeyDown={onKeyDown}
      >
        {node.kind !== 'start' && (
          <span className="studio-node-port studio-node-port-in" aria-hidden="true" />
        )}
        <Icon name={KIND_ICONS[node.kind]} className="studio-node-icon" />
        <span className="studio-node-text">
          <span className="studio-node-id">{node.id}</span>
          {!terminal && <span className="studio-node-detail">{detail}</span>}
        </span>
        {problems?.length ? (
          <>
            <span className="studio-node-flag" aria-hidden="true" />
            <span id={describedBy} className="sr-only">
              {problems.join(' ')}
            </span>
          </>
        ) : null}
      </div>
      {node.kind !== 'end' && (
        <button
          type="button"
          className={`studio-node-port studio-node-port-out ${connectingFrom ? 'active' : ''}`}
          style={{
            transform: `translate(${position.x + NODE_WIDTH - PORT_RADIUS}px, ${
              position.y + NODE_HEIGHT / 2 - PORT_RADIUS
            }px)`,
          }}
          data-node-id={node.id}
          aria-label={`Connect from ${node.id}`}
          aria-pressed={connectingFrom}
          onPointerDown={onPortPointerDown}
          onPointerMove={onPortPointerMove}
          onPointerUp={onPortPointerUp}
          onPointerCancel={onPortPointerUp}
          onClick={(event) => {
            // A pointer press is handled on pointer up; a keyboard activation arrives as a
            // click with no pointer gesture behind it.
            if (event.detail === 0) onPortToggle();
          }}
        />
      )}
    </>
  );
}
