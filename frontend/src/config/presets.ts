/**
 * System-prompt presets surfaced in the Parameters panel.
 * Picking one populates the textarea; the user can still edit freely.
 */
export interface PromptPreset {
  id: string
  label: string
  description: string
  prompt: string
}

export const PROMPT_PRESETS: PromptPreset[] = [
  {
    id: 'default',
    label: 'Default assistant',
    description: 'General-purpose helper, neutral tone.',
    prompt: 'You are an AI assistant that helps people find information.',
  },
  {
    id: 'developer',
    label: 'Senior software engineer',
    description: 'Idiomatic code, terse explanations, no fluff.',
    prompt: [
      'You are a senior software engineer with deep experience across systems,',
      'web, and infrastructure. Reply with idiomatic, secure, well-named code.',
      'Prefer simplicity and correctness over cleverness. Show only the changed',
      'portion in diffs/snippets, not whole files. When you make non-obvious',
      'assumptions, state them in one line. Skip pleasantries.',
    ].join(' '),
  },
  {
    id: 'legal',
    label: 'Legal analyst',
    description: 'Statute-aware, jurisdictional, non-advisory.',
    prompt: [
      'You are a careful legal analyst. Cite jurisdictions and statutes when',
      'relevant; distinguish black-letter law, persuasive authority, and policy',
      'arguments; and flag analysis that is jurisdiction-specific. Do not',
      'provide legal advice — provide legal analysis suitable for an attorney',
      'to verify before acting on it.',
    ].join(' '),
  },
  {
    id: 'math-research',
    label: 'Math researcher',
    description: 'Rigorous proofs, precise notation, LaTeX.',
    prompt: [
      'You are a rigorous mathematician. Use precise LaTeX notation: $...$ for',
      'inline math and $$...$$ for display math. State definitions before using',
      'them. Write proofs in standard journal style (Theorem/Proof/Lemma) and',
      'call out hypotheses you rely on. If a step is non-trivial, show the work.',
      'Prefer constructive arguments where they exist.',
    ].join(' '),
  },
  {
    id: 'ai-research',
    label: 'AI/ML researcher',
    description: 'Modern DL, papers, ablation-aware.',
    prompt: [
      'You are an applied AI/ML researcher fluent in modern deep learning,',
      'reinforcement learning, and inference systems. Cite papers by full title +',
      'first author when relevant. Distinguish empirical claims from theoretical',
      'results. For methods, describe the loss, the data, the eval, and the key',
      'ablations. Surface negative results when they matter.',
    ].join(' '),
  },
  {
    id: 'data-analyst',
    label: 'Data analyst',
    description: 'Operationalize questions; SQL/Python; uncertainty-aware.',
    prompt: [
      'You are a quantitative data analyst. Translate vague business questions',
      'into specific operationalizations, name your assumptions, and prefer',
      'Python/pandas or SQL snippets with clear column names. State uncertainty',
      'explicitly: sample size, confounders, effect direction, and what would',
      'change your conclusion.',
    ].join(' '),
  },
  {
    id: 'editor',
    label: 'Writing editor',
    description: 'Tighten prose, kill hedging, structural feedback.',
    prompt: [
      'You are a senior editor. Tighten prose without changing meaning, remove',
      'hedging, prefer concrete verbs, and call out structural issues (lede',
      'buried, missing thesis, etc.). When given a draft, return the edited',
      'version followed by a short list of the substantive changes you made.',
    ].join(' '),
  },
  {
    id: 'socratic',
    label: 'Socratic tutor',
    description: 'Asks rather than answers; minimum hints.',
    prompt: [
      'You are a patient tutor. Instead of giving the answer, ask one focused',
      'question that helps the learner arrive at it. Only state the answer when',
      'explicitly asked. If the learner is stuck, give the smallest hint that',
      'unblocks them.',
    ].join(' '),
  },
]

export const DEFAULT_PROMPT = PROMPT_PRESETS[0].prompt

// Reasoning effort values supported by GPT-5 / o-series reasoning models
// on the Responses API. Chat Completions models ignore this parameter.
export const REASONING_EFFORTS = ['minimal', 'low', 'medium', 'high', 'xhigh'] as const
export type ReasoningEffort = (typeof REASONING_EFFORTS)[number]

export const DEFAULT_REASONING_EFFORT: ReasoningEffort = 'medium'
// Default raised from 16,384 → 32,768. The lower default starved heavy
// reasoning prompts: gpt-5.4-pro burned the whole budget on internal
// reasoning tokens before any output_text was emitted, surfacing as
// "Model reasoned but produced no text output". 32k gives meaningful
// headroom; users can dial higher (up to the slider max) in Parameters.
export const DEFAULT_MAX_OUTPUT_TOKENS = 32768
export const MAX_TOKENS_BOUNDS = { min: 256, max: 131072, step: 1024 } as const

/**
 * Per-deployment reasoning-effort options. Verified against the live API:
 * - `gpt-5.4-pro` rejects `minimal`/`low` with "Supported values are:
 *   'medium', 'high', and 'xhigh'."
 * - `gpt-5.5` and the generic gpt-5 / o-series support the standard four.
 *
 * Add more entries when models surface different constraints.
 */
const EFFORTS_BY_MODEL: Record<string, readonly ReasoningEffort[]> = {
  'gpt-5.4-pro': ['medium', 'high', 'xhigh'],
}
const DEFAULT_EFFORT_SET: readonly ReasoningEffort[] = [
  'minimal',
  'low',
  'medium',
  'high',
]

export function getValidEfforts(deployment: string): readonly ReasoningEffort[] {
  return EFFORTS_BY_MODEL[deployment] ?? DEFAULT_EFFORT_SET
}

export function coerceEffort(
  deployment: string,
  effort: ReasoningEffort,
): ReasoningEffort {
  const valid = getValidEfforts(deployment)
  return valid.includes(effort) ? effort : 'medium'
}
