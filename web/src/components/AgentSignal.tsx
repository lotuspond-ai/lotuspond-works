// A small looping glyph for each kind of agent Lotuspond can evaluate. Decorative only:
// the text beside it carries the meaning, and reduced motion stops every animation.
export type SignalKind = 'http' | 'protocol' | 'voice' | 'software';

export function AgentSignal({ kind }: { kind: SignalKind }) {
  return (
    <svg
      className={`agent-signal agent-signal-${kind}`}
      viewBox="0 0 120 36"
      width="120"
      height="36"
      aria-hidden="true"
      focusable="false"
    >
      {kind === 'http' && (
        <>
          <circle cx="8" cy="18" r="4" className="signal-node" />
          <circle cx="112" cy="18" r="4" className="signal-node" />
          <line x1="14" y1="18" x2="106" y2="18" className="signal-wire" />
          <circle cx="14" cy="18" r="2.6" className="signal-pulse signal-request" />
          <circle cx="106" cy="18" r="2.6" className="signal-pulse signal-reply" />
        </>
      )}
      {kind === 'protocol' && (
        <>
          <rect x="3" y="11" width="16" height="14" rx="3" className="signal-node" />
          <rect x="101" y="11" width="16" height="14" rx="3" className="signal-node" />
          <path d="M22 13 C 48 2, 72 2, 98 13" pathLength={100} className="signal-wire" />
          <path d="M22 13 C 48 2, 72 2, 98 13" pathLength={100} className="signal-flow" />
          <path d="M98 23 C 72 34, 48 34, 22 23" pathLength={100} className="signal-wire" />
          <path
            d="M98 23 C 72 34, 48 34, 22 23"
            pathLength={100}
            className="signal-flow signal-flow-back"
          />
        </>
      )}
      {kind === 'voice' && (
        <g className="signal-wave">
          {Array.from({ length: 19 }, (_, index) => (
            <rect
              key={index}
              x={4 + index * 6}
              y="6"
              width="3"
              height="24"
              rx="1.5"
              style={{ animationDelay: `${(index % 7) * -0.13}s` }}
            />
          ))}
        </g>
      )}
      {kind === 'software' && (
        <>
          <rect x="2" y="3" width="116" height="30" rx="4" className="signal-frame" />
          <circle cx="9" cy="9" r="1.6" className="signal-dot" />
          <circle cx="15" cy="9" r="1.6" className="signal-dot" />
          <circle cx="21" cy="9" r="1.6" className="signal-dot" />
          <path d="M9 22 l4 3 -4 3" className="signal-prompt" />
          <line x1="18" y1="25" x2="84" y2="25" pathLength={100} className="signal-typed" />
          <rect x="86" y="20" width="5" height="9" className="signal-cursor" />
        </>
      )}
    </svg>
  );
}
