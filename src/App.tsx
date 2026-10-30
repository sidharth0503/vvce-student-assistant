import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import ErrorBoundary from './ErrorBoundary'
import {
  ArrowUp, BookOpen, CalendarDays, Check, CircleAlert, CircleHelp, GraduationCap,
  LoaderCircle, Plus, Sparkles, Trash2,
} from 'lucide-react'

type Source = {
  url: string
  title: string
  date?: string | null
  source_type?: 'manual' | 'crawled'
}
type Message = { role: 'user' | 'assistant'; text: string; sources?: Source[]; error?: boolean }
type Health = {
  status: string
  ready?: boolean
  knowledge_base_ready?: boolean
  indexed_chunks?: number
  documents?: number
  manual_chunks?: number
  crawled_chunks?: number
  gemini_configured?: boolean
  llm_configured?: boolean
}

const suggestions = [
  { text: 'Where can I find the academic calendar?', icon: <CalendarDays size={17} /> },
  { text: 'What are the departments and courses at VVCE?', icon: <BookOpen size={17} /> },
  { text: 'Tell me about admissions and eligibility', icon: <GraduationCap size={17} /> },
]

function normalizeSources(value: unknown): Source[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((source): Source[] => {
    if (!source || typeof source !== 'object') return []
    const row = source as Record<string, unknown>
    if (typeof row.url !== 'string' || typeof row.title !== 'string') return []
    try {
      const parsed = new URL(row.url)
      if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return []
    } catch { return [] }
    return [{
      url: row.url.slice(0, 2048),
      title: row.title.slice(0, 300),
      date: typeof row.date === 'string' ? row.date : null,
      source_type: row.source_type === 'manual' ? 'manual' : 'crawled',
    }]
  })
}

function normalizeHealth(value: unknown): Health | null {
  if (!value || typeof value !== 'object') return null
  const row = value as Record<string, unknown>
  const numberOrUndefined = (item: unknown) => typeof item === 'number' && Number.isFinite(item) && item >= 0 ? item : undefined
  return {
    status: typeof row.status === 'string' ? row.status : 'unknown',
    ready: typeof row.ready === 'boolean' ? row.ready : undefined,
    knowledge_base_ready: typeof row.knowledge_base_ready === 'boolean' ? row.knowledge_base_ready : undefined,
    indexed_chunks: numberOrUndefined(row.indexed_chunks),
    documents: numberOrUndefined(row.documents),
    manual_chunks: numberOrUndefined(row.manual_chunks),
    crawled_chunks: numberOrUndefined(row.crawled_chunks),
    gemini_configured: typeof row.gemini_configured === 'boolean' ? row.gemini_configured : undefined,
    llm_configured: typeof row.llm_configured === 'boolean' ? row.llm_configured : undefined,
  }
}

function ChatInterface() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [health, setHealth] = useState<Health | null>(null)
  const [healthChecked, setHealthChecked] = useState(false)
  const bottom = useRef<HTMLDivElement>(null)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const chatRequest = useRef<AbortController | null>(null)

  useEffect(() => {
    const controller = new AbortController()

    async function fetchHealth() {
      try {
        const response = await fetch('/api/health', { signal: controller.signal })
        if (!response.ok) throw new Error('Backend health check failed')
        const nextHealth = normalizeHealth(await response.json())
        if (!controller.signal.aborted) setHealth(nextHealth)
      } catch (error) {
        if (!controller.signal.aborted && !(error instanceof Error && error.name === 'AbortError')) {
          setHealth(null)
        }
      } finally {
        if (!controller.signal.aborted) setHealthChecked(true)
      }
    }

    // Defer the first request until after React's StrictMode setup/cleanup probe.
    // The probe's first controller is already aborted by the time this microtask runs.
    void Promise.resolve().then(() => {
      if (!controller.signal.aborted) void fetchHealth()
    })
    const timer = window.setInterval(() => void fetchHealth(), 15000)
    return () => {
      controller.abort()
      chatRequest.current?.abort()
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, loading])

  async function send(event?: FormEvent, suggestedText?: string) {
    event?.preventDefault()
    const question = (suggestedText ?? input).trim()
    if (!question || loading) return
    setInput('')
    if (textarea.current) textarea.current.style.height = 'auto'
    setMessages(old => [...old, { role: 'user', text: question }])
    setLoading(true)
    const controller = new AbortController()
    chatRequest.current = controller
    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          message: question,
          history: messages.filter(message => !message.error).slice(-8)
            .map(message => ({ role: message.role, content: message.text })),
        }),
      })
      let payload: unknown
      try { payload = await response.json() }
      catch { throw new Error('The backend returned an unreadable response. Please try again.') }
      if (controller.signal.aborted) return
      const body = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {}
      if (!response.ok) {
        const detail = typeof body.detail === 'string' ? body.detail
          : typeof body.answer === 'string' ? body.answer : 'The assistant could not respond right now.'
        throw new Error(detail)
      }
      if (typeof body.answer !== 'string' || !body.answer.trim()) {
        throw new Error('The assistant returned an unreadable answer. Please try again.')
      }
      const answer = body.answer
      setMessages(old => [...old, {
        role: 'assistant', text: answer.slice(0, 20000),
        sources: normalizeSources(body.sources),
      }])
    } catch (error) {
      if (controller.signal.aborted) return
      setMessages(old => [...old, {
        role: 'assistant', error: true,
        text: error instanceof Error ? error.message : 'Connection failed. Check the backend and try again.',
      }])
    } finally {
      if (chatRequest.current === controller) {
        chatRequest.current = null
        setLoading(false)
        textarea.current?.focus()
      }
    }
  }

  function clearConversation() {
    chatRequest.current?.abort()
    chatRequest.current = null
    setMessages([])
    setLoading(false)
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void send()
    }
  }

  const backendOnline = healthChecked && health !== null
  const knowledgeReady = Boolean(health?.knowledge_base_ready ?? health?.ready)
  const chunkCount = health?.indexed_chunks ?? health?.documents ?? 0

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark"><img className="brand-logo" src="/vvce-logo.png" alt="Vidyavardhaka College of Engineering logo" /></div>
        <div><strong>VVCE</strong><span>STUDENT ASSISTANT</span></div>
      </div>
      <button className="new-chat" onClick={clearConversation}><Plus size={17} /> New conversation</button>
      <div className="side-label">EXPLORE VVCE</div>
      <a className="side-link" href="https://vvce.ac.in/academic-notifications/" target="_blank" rel="noreferrer"><CalendarDays size={17} /> Academic notices</a>
      <a className="side-link" href="https://vvce.ac.in/departments/" target="_blank" rel="noreferrer"><BookOpen size={17} /> Departments</a>
      <a className="side-link" href="https://vvce.ac.in/admissions/" target="_blank" rel="noreferrer"><GraduationCap size={17} /> Admissions</a>

      <div className="sidebar-bottom">
        <div className={`status-dot ${backendOnline && knowledgeReady ? 'is-ready' : 'is-offline'}`} />
        <span>{backendOnline ? knowledgeReady ? `${chunkCount.toLocaleString()} chunks ready` : 'Knowledge base not ready' : healthChecked ? 'Backend offline' : 'Checking backend…'}</span>
        <small>{health?.manual_chunks ?? 0} local · {health?.crawled_chunks ?? 0} crawled chunks</small>
      </div>
    </aside>

    <main className="main">
      <header className="topbar">
        <img className="top-logo" src="/vvce-logo.png" alt="Vidyavardhaka College of Engineering logo" />
        <div className="top-heading">
          <div className="top-title">VVCE Student Assistant</div>
          <div className="top-sub">Your guide to college information</div>
        </div>
        <div className={`health-pill ${!backendOnline ? 'health-offline' : knowledgeReady ? 'health-ready' : 'health-warning'}`} title={(health?.llm_configured ?? health?.gemini_configured) === false ? 'No LLM provider is configured' : undefined}>
          <i />
          <span>{!backendOnline ? healthChecked ? 'Backend offline' : 'Connecting…' : knowledgeReady ? 'Assistant ready' : 'Knowledge base empty'}</span>
          {backendOnline && knowledgeReady && <Check size={13} />}
        </div>
        {messages.length > 0 && <button className="clear-chat" onClick={clearConversation} title="Clear conversation"><Trash2 size={15} /><span>Clear chat</span></button>}
        {messages.length === 0 && <button className="clear-chat mobile-new" onClick={() => { clearConversation(); textarea.current?.focus() }} title="Start new conversation"><Plus size={16} /><span>New chat</span></button>}
      </header>

      <section className={`conversation ${messages.length ? 'has-messages' : ''}`} aria-live="polite">
        {messages.length === 0 ? <div className="welcome">
          <img className="welcome-logo" src="/vvce-logo.png" alt="Vidyavardhaka College of Engineering logo" />
          <div className="eyebrow">VIDYAVARDHAKA COLLEGE OF ENGINEERING</div>
          <h1>What can I help<br />you find today?</h1>
          <p>Ask about academic notices, calendars, departments, admissions, placements and student services.</p>
          {!backendOnline && healthChecked && <div className="readiness-note"><CircleAlert size={15} /> Start the FastAPI backend to chat.</div>}
          {backendOnline && !knowledgeReady && <div className="readiness-note"><CircleAlert size={15} /> The backend is online, but its knowledge base has no indexed chunks yet.</div>}
        {backendOnline && (health?.llm_configured ?? health?.gemini_configured) === false && <div className="readiness-note"><CircleHelp size={15} /> Add <code>GROQ_API_KEY</code> to <code>backend/.env</code> to enable generated answers. <code>GEMINI_API_KEY</code> enables fallback.</div>}
          <div className="suggestions">
            {suggestions.map(item => <button key={item.text} onClick={() => void send(undefined, item.text)} disabled={loading || !knowledgeReady}>
              <span>{item.icon}</span>{item.text}<b>↗</b>
            </button>)}
          </div>
        </div> : <div className="message-list">
          {messages.map((message, index) => <article role={message.error ? 'alert' : undefined} className={`message ${message.role} ${message.error ? 'message-error' : ''}`} key={`${index}-${message.role}`}>
            <div className="avatar">{message.error ? <CircleAlert size={16} /> : message.role === 'assistant' ? <GraduationCap size={17} /> : 'Y'}</div>
            <div className="message-body">
              <div className="role-name">{message.role === 'assistant' ? 'VVCE Assistant' : 'You'}</div>
              {message.role === 'assistant' && !message.error
                ? <div className="message-text markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
                  a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer">{children}</a>,
                }}>{message.text}</ReactMarkdown></div>
                : <div className="message-text">{message.text}</div>}
              {message.sources?.length ? <div className="sources">
                <span>Sources</span>
                {message.sources.map((source, sourceIndex) => <a key={`${source.url}-${source.source_type}-${sourceIndex}`} href={source.url} target="_blank" rel="noreferrer">
                  <i>{source.source_type === 'manual' ? 'Local document' : 'VVCE source'}</i>
                  {source.title}{source.date ? ` · ${source.date}` : ''} ↗
                </a>)}
              </div> : null}
            </div>
          </article>)}
          {loading && <div className="loading"><LoaderCircle className="spin" size={18} /> Searching VVCE sources and preparing an answer…</div>}
          <div ref={bottom} />
        </div>}
      </section>

      <footer className="composer-wrap">
        <form className="composer" onSubmit={event => void send(event)}>
          <textarea ref={textarea} value={input} onChange={event => {
            setInput(event.target.value)
            event.target.style.height = 'auto'
            event.target.style.height = `${Math.min(event.target.scrollHeight, 130)}px`
          }} onKeyDown={onKeyDown} placeholder="Ask anything about VVCE…" rows={1} disabled={loading} aria-label="Ask a question about VVCE" />
          <div className="composer-foot">
            <span><Sparkles size={13} /> Answers include links to original sources</span>
            <button disabled={!input.trim() || loading} aria-label="Send message">{loading ? <LoaderCircle className="spin" size={17} /> : <ArrowUp size={18} />}</button>
          </div>
        </form>
        <div className="disclaimer">AI can make mistakes. Verify important dates and notices on the official VVCE website.</div>
      </footer>
    </main>
  </div>
}

export default function App() {
  return <ErrorBoundary><ChatInterface /></ErrorBoundary>
}
