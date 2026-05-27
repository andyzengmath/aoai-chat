/**
 * Empty-state showpiece: a CSS-rendered black hole with rotating accretion disk
 * and gravitational halo. Pure CSS (no SVG), so it scales cheaply.
 *
 * Anatomy:
 *   - Outer halo: soft radial bloom (warm amber → fade to nothing)
 *   - Accretion disk: conic-gradient ring with rotation, perspective-tilted
 *   - Inner ring (Einstein ring): brighter blur
 *   - Event horizon: pure black disk with inset shadow
 *   - Photon sphere: thin highlight just outside the horizon
 *
 * The whole thing is wrapped in a perspective container so the disk reads as
 * a tilted 3D ring rather than a flat circle.
 */
export function BlackHole({ size = 360 }: { size?: number }) {
  return (
    <div
      className="relative mx-auto"
      style={{ width: size, height: size, perspective: '1200px' }}
      aria-hidden
    >
      {/* Outer gravitational bloom */}
      <div
        className="absolute inset-0"
        style={{
          background:
            'radial-gradient(circle, rgba(244,184,96,0.18) 0%, rgba(244,184,96,0.05) 30%, transparent 60%)',
          filter: 'blur(28px)',
        }}
      />

      {/* Outer accretion disk (rotating, perspective-tilted, color-stratified) */}
      <div
        className="absolute inset-0 black-hole-disk"
        style={{
          borderRadius: '50%',
          background:
            'conic-gradient(from 200deg, rgba(255,123,92,0.0) 0%, rgba(255,123,92,0.85) 12%, rgba(244,184,96,1) 22%, rgba(255,255,255,0.95) 30%, rgba(244,184,96,0.95) 38%, rgba(255,123,92,0.75) 55%, rgba(217,70,239,0.6) 78%, rgba(255,123,92,0.0) 100%)',
          filter: 'blur(10px) saturate(115%)',
          transform: 'rotateX(72deg)',
          maskImage:
            'radial-gradient(circle, transparent 32%, black 35%, black 58%, transparent 68%)',
          WebkitMaskImage:
            'radial-gradient(circle, transparent 32%, black 35%, black 58%, transparent 68%)',
        }}
      />

      {/* Inner Einstein ring — brighter, thinner */}
      <div
        className="absolute inset-0 black-hole-ring"
        style={{
          borderRadius: '50%',
          background:
            'conic-gradient(from 220deg, rgba(255,123,92,0.0), rgba(255,235,200,1) 25%, rgba(255,255,255,1) 33%, rgba(244,184,96,0.95) 42%, rgba(255,123,92,0.0) 70%)',
          filter: 'blur(3px) saturate(110%)',
          transform: 'rotateX(72deg) scale(0.96)',
          maskImage:
            'radial-gradient(circle, transparent 33%, black 34%, black 36%, transparent 38%)',
          WebkitMaskImage:
            'radial-gradient(circle, transparent 33%, black 34%, black 36%, transparent 38%)',
        }}
      />

      {/* Event horizon (the black disk) + photon sphere highlight */}
      <div className="absolute inset-0 flex items-center justify-center">
        <div
          className="rounded-full bg-black"
          style={{
            width: size * 0.32,
            height: size * 0.32,
            boxShadow:
              '0 0 30px 8px rgba(255,123,92,0.45), 0 0 70px 20px rgba(217,70,239,0.18), inset 0 0 24px rgba(0,0,0,1), 0 0 0 1px rgba(255,235,200,0.35)',
          }}
        />
      </div>
    </div>
  )
}
