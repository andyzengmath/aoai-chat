import clsx from 'clsx'
import { Check, Copy } from 'lucide-react'
import { memo, useState, type ComponentProps } from 'react'

/**
 * Renders inline code as <code> and fenced code blocks as a chrome-wrapped
 * <pre><code>. Syntax highlighting comes from rehype-highlight (lowlight)
 * which injects `language-xxx` classes that `highlight.js` theme styles.
 */
type CodeProps = ComponentProps<'code'> & { inline?: boolean }

export const CodeBlock = memo(function CodeBlock({
  inline,
  className,
  children,
  ...props
}: CodeProps) {
  const [copied, setCopied] = useState(false)

  // react-markdown invokes the same renderer for both inline and fenced code.
  // We treat single-line, no-language strings as inline.
  const text = Array.isArray(children) ? children.join('') : String(children ?? '')
  // rehype-highlight prepends `hljs` so the language class may not be at start.
  const langMatch = /(?:^|\s)language-([\w+-]+)/.exec(className || '')
  const lang = langMatch?.[1]
  const isInline = inline ?? (!lang && !text.includes('\n'))

  if (isInline) {
    return (
      <code
        className="px-1.5 py-0.5 rounded-md bg-white/10 border border-white/10 text-[0.85em] font-mono text-cyan-200"
        {...props}
      >
        {children}
      </code>
    )
  }

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(text.replace(/\n$/, ''))
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard unavailable; ignore */
    }
  }

  return (
    <div className="relative my-4 rounded-xl border border-white/10 overflow-hidden bg-black/40">
      <div className="flex items-center justify-between px-3 py-1.5 text-[10px] font-mono uppercase tracking-wider text-white/40 border-b border-white/5">
        <span>{lang || 'text'}</span>
        <button
          onClick={onCopy}
          className="flex items-center gap-1 px-2 py-1 rounded-md hover:bg-white/5 transition-colors text-white/50 hover:text-white/80"
        >
          {copied ? (
            <>
              <Check size={11} className="text-emerald-400" /> copied
            </>
          ) : (
            <>
              <Copy size={11} /> copy
            </>
          )}
        </button>
      </div>
      <pre className={clsx('overflow-x-auto p-3 text-[13px] leading-relaxed font-mono', className)}>
        <code className={className} {...props}>
          {children}
        </code>
      </pre>
    </div>
  )
})
