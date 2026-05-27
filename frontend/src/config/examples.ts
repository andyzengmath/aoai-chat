/**
 * Quick-start example prompts shown as chips on the empty state.
 * Clicking one sends the prompt immediately — saves the user from
 * staring at a blinking cursor.
 */
export interface ExamplePrompt {
  category: string
  label: string
  prompt: string
}

export const EXAMPLE_PROMPTS: readonly ExamplePrompt[] = [
  {
    category: 'Math',
    label: 'Derive the Fourier transform of a Gaussian',
    prompt:
      'Derive the Fourier transform of a Gaussian e^{-x^2}. Show every step in LaTeX, terse.',
  },
  {
    category: 'Reasoning',
    label: 'Strongest argument against many-worlds',
    prompt:
      'What is the strongest argument against the many-worlds interpretation of quantum mechanics? Two paragraphs.',
  },
  {
    category: 'Code',
    label: 'Implement an LRU cache with TTL in Python',
    prompt:
      'Write a thread-safe Python LRU cache with per-entry TTL. Include type hints and one usage example.',
  },
  {
    category: 'Writing',
    label: 'Tighten this paragraph',
    prompt:
      'Edit the following paragraph for clarity and concision. Return the edited version plus a short list of substantive changes. Paragraph: ',
  },
] as const
