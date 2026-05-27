/**
 * Pondering phrases shown while the model thinks (before any reasoning summary
 * or output tokens stream). Rotated every ~2.4s in display italic.
 *
 * Tone: scholarly, slightly playful, math/physics/CS-flavored. NOT generic
 * "thinking…" — this is a tool for someone who appreciates a good pun about
 * étale cohomology while waiting for gpt-5.4-pro to finish its proof.
 */
export const PONDERING_PHRASES: readonly string[] = [
  'calculating contour integrals',
  'proving the Riemann hypothesis',
  'solving the Navier–Stokes equations',
  'computing étale cohomology',
  'diagonalizing the Hamiltonian',
  'factoring a Carmichael number',
  'taking a Fourier transform',
  'tracing the heat kernel',
  'verifying Goldbach by hand',
  'unifying gravity and quantum mechanics',
  'minimizing the action',
  'extending to the algebraic closure',
  'computing a Gröbner basis',
  'sheafifying a presheaf',
  'inverting the Laplace transform',
  'lifting to the universal cover',
  'estimating the Selberg zeta',
  'untangling a braid group',
  'localizing at a maximal ideal',
  'finding the saddle point',
  'integrating by parts, again',
  'walking a random graph',
  'measuring a set of measure zero',
  'spelunking in Hilbert space',
  'consulting the L-function',
  'searching for monsters in moonshine',
  'partitioning the zeta zeros',
  'glueing schemes',
  'evaluating an Euler product',
  'reading the comments in Cassels & Fröhlich',
  'compactifying the moduli space',
  'taking the long exact sequence',
  'descending along a torsor',
  'twisting by a character',
  'unfolding the spectral sequence',
  'computing a Borel–Moore class',
  'tensoring with a flat module',
  'reducing modulo p',
  'reading Tao’s blog, very carefully',
  'simulating the universe in your head',
] as const

export function pickPondering(prev?: string): string {
  if (PONDERING_PHRASES.length === 0) return 'thinking'
  for (let i = 0; i < 5; i++) {
    const next = PONDERING_PHRASES[Math.floor(Math.random() * PONDERING_PHRASES.length)]
    if (next !== prev) return next
  }
  return PONDERING_PHRASES[0]
}
