/**
 * Decorative SVG: a tiny "constellation" of mathematical glyphs connected by
 * thin lines, used as the empty-state anchor. Slow drift via CSS keyframes;
 * respects prefers-reduced-motion via the parent `.aurora-drift-*` rules.
 */
const NODES = [
  { x: 40, y: 92, glyph: '∫' },
  { x: 110, y: 36, glyph: '∑' },
  { x: 170, y: 110, glyph: '∂' },
  { x: 245, y: 50, glyph: 'ℝ' },
  { x: 305, y: 100, glyph: 'ζ' },
  { x: 360, y: 38, glyph: 'π' },
]

export function Constellation() {
  return (
    <svg
      viewBox="0 0 400 150"
      width="400"
      height="150"
      className="text-amber-200/40"
      aria-hidden
    >
      <defs>
        <linearGradient id="conn-grad" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0" />
          <stop offset="50%" stopColor="currentColor" stopOpacity="0.6" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      {/* connecting lines */}
      {NODES.slice(0, -1).map((n, i) => {
        const m = NODES[i + 1]
        return (
          <line
            key={`l${i}`}
            x1={n.x}
            y1={n.y}
            x2={m.x}
            y2={m.y}
            stroke="url(#conn-grad)"
            strokeWidth="0.6"
            strokeDasharray="2 4"
          />
        )
      })}
      {/* glyph nodes */}
      {NODES.map((n, i) => (
        <g key={`g${i}`} style={{ transformOrigin: `${n.x}px ${n.y}px` }}>
          <circle
            cx={n.x}
            cy={n.y}
            r="14"
            fill="rgba(0,0,0,0.4)"
            stroke="currentColor"
            strokeOpacity="0.25"
            strokeWidth="0.5"
          />
          <text
            x={n.x}
            y={n.y + 6}
            textAnchor="middle"
            className="font-display italic"
            fill="currentColor"
            fillOpacity="0.85"
            style={{
              fontSize: '17px',
              fontFamily: '"Fraunces Variable", "Fraunces", Georgia, serif',
              fontVariationSettings: '"opsz" 144, "SOFT" 30',
            }}
          >
            {n.glyph}
          </text>
        </g>
      ))}
    </svg>
  )
}
