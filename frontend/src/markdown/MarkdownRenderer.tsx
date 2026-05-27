import { memo, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'

import { CodeBlock } from './CodeBlock'

interface Props {
  content: string
}

/**
 * Normalize LaTeX math delimiters that models commonly emit but `remark-math`
 * doesn't recognize by default: `\[ ... \]` -> `$$ ... $$`, `\( ... \)` -> `$ ... $`.
 * Skips replacements inside fenced code blocks (``` ... ```) and inline code (` ... `)
 * so example LaTeX in code samples stays intact.
 */
function normalizeMathDelimiters(src: string): string {
  // Split on code fences / inline-code regions; transform only outside-code chunks.
  const parts = src.split(/(```[\s\S]*?```|`[^`\n]+`)/g)
  return parts
    .map((chunk, i) => {
      if (i % 2 === 1) return chunk // code chunk — leave untouched
      return chunk
        // Block math: \[ ... \] (DOTALL semantics via [\s\S])
        .replace(/\\\[\s*([\s\S]+?)\s*\\\]/g, (_m, body) => `\n$$\n${body}\n$$\n`)
        // Inline math: \( ... \)
        .replace(/\\\(\s*([\s\S]+?)\s*\\\)/g, (_m, body) => `$${body}$`)
    })
    .join('')
}

const components: Components = {
  code: CodeBlock as Components['code'],
  p: ({ children }) => <p className="my-2 leading-relaxed first:mt-0 last:mb-0">{children}</p>,
  h1: ({ children }) => <h1 className="text-xl font-semibold tracking-tight mt-5 mb-3">{children}</h1>,
  h2: ({ children }) => <h2 className="text-lg font-semibold tracking-tight mt-4 mb-2">{children}</h2>,
  h3: ({ children }) => <h3 className="text-base font-semibold tracking-tight mt-3 mb-2">{children}</h3>,
  h4: ({ children }) => <h4 className="text-sm font-semibold mt-3 mb-1">{children}</h4>,
  ul: ({ children }) => <ul className="my-2 ml-5 list-disc space-y-1">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 ml-5 list-decimal space-y-1">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="my-3 pl-3 border-l-2 border-cyan-400/40 text-white/70 italic">
      {children}
    </blockquote>
  ),
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-cyan-300 underline underline-offset-2 hover:text-cyan-200"
    >
      {children}
    </a>
  ),
  strong: ({ children }) => <strong className="font-semibold text-white">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  hr: () => <hr className="my-4 border-white/10" />,
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto">
      <table className="border-collapse text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => <th className="px-3 py-1.5 text-left border-b border-white/10 font-medium">{children}</th>,
  td: ({ children }) => <td className="px-3 py-1.5 border-b border-white/5">{children}</td>,
}

export const MarkdownRenderer = memo(function MarkdownRenderer({ content }: Props): ReactNode {
  const normalized = normalizeMathDelimiters(content)
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[
        rehypeKatex,
        // Skip highlighting on entries without a language class (avoids false-positive
        // on inline code).
        [rehypeHighlight, { detect: true, ignoreMissing: true }],
      ]}
      components={components}
    >
      {normalized}
    </ReactMarkdown>
  )
})
