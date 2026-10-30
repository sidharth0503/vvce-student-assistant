// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import App from './App'
import ErrorBoundary from './ErrorBoundary'

function jsonResponse(payload: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => payload }
}

describe('chat API errors', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('scrollTo', vi.fn())
    Element.prototype.scrollIntoView = vi.fn()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    fetchMock.mockReset()
  })

  it('renders a visible, helpful message when the chat API returns an error', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({
        status: 'ok', ready: true, knowledge_base_ready: true, indexed_chunks: 12,
        manual_chunks: 4, crawled_chunks: 8, gemini_configured: true,
      }))
      .mockResolvedValueOnce(jsonResponse({ detail: 'Gemini timed out within 90 seconds. Please try again.' }, 504))

    render(<App />)
    const input = await screen.findByRole('textbox', { name: 'Ask a question about VVCE' })
    fireEvent.change(input, { target: { value: 'When is the academic calendar?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('Gemini timed out within 90 seconds. Please try again.')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('shows an error instead of crashing when a successful response has a malformed answer', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ status: 'ok', knowledge_base_ready: true, indexed_chunks: 5 }))
      .mockResolvedValueOnce(jsonResponse({ answer: { unexpected: 'object' }, sources: [] }))
    render(<App />)
    const input = await screen.findByRole('textbox', { name: 'Ask a question about VVCE' })
    fireEvent.change(input, { target: { value: 'What is VVCE?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText(/returned an unreadable answer/i)).toBeTruthy()
  })

  it('renders Markdown citations and sends recent conversation context on follow-up', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({
        status: 'ok', ready: true, knowledge_base_ready: true, indexed_chunks: 12,
        manual_chunks: 4, crawled_chunks: 8, llm_configured: true,
      }))
      .mockResolvedValueOnce(jsonResponse({
        answer: 'VVCE offers the **Information Science and Engineering** department. [Department source](https://vvce.ac.in/departments/department-of-information-science-engineering)',
        sources: [
          { url: 'https://vvce.ac.in/departments/department-of-information-science-engineering', title: 'ISE', date: null },
          { url: { malformed: true }, title: { malformed: true } }, null,
        ],
      }))
      .mockResolvedValueOnce(jsonResponse({ answer: 'Its page also describes student clubs.', sources: [] }))

    render(<App />)
    const input = await screen.findByRole('textbox', { name: 'Ask a question about VVCE' })
    fireEvent.change(input, { target: { value: 'What is ISE?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByText(/Information Science and Engineering/)).toBeTruthy()
    const citation = await screen.findByRole('link', { name: 'Department source' })
    expect(citation.getAttribute('href')).toBe('https://vvce.ac.in/departments/department-of-information-science-engineering')

    fireEvent.change(input, { target: { value: 'What about its clubs?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
    expect(await screen.findByText('Its page also describes student clubs.')).toBeTruthy()
    const chatCalls = fetchMock.mock.calls.filter(([url]) => url === '/api/chat')
    const request = JSON.parse(chatCalls[1][1].body)
    expect(request.history).toEqual([
      { role: 'user', content: 'What is ISE?' },
      { role: 'assistant', content: expect.stringContaining('Information Science and Engineering') },
    ])
  })

  it('runs health effects safely under React StrictMode mount and cleanup', async () => {
    const reactErrors = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    Element.prototype.scrollIntoView = vi.fn(() => Promise.resolve())
    let resolveRequest: ((value: ReturnType<typeof jsonResponse>) => void) | undefined
    fetchMock.mockImplementation((_url: string, _options: { signal: AbortSignal }) =>
      new Promise(resolve => { resolveRequest = resolve }))
    const view = render(<StrictMode><App /></StrictMode>)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    const activeSignal = fetchMock.mock.calls[0][1].signal as AbortSignal
    expect(activeSignal.aborted).toBe(false)

    view.unmount()
    expect(activeSignal.aborted).toBe(true)
    await act(async () => {
      resolveRequest?.(jsonResponse({ status: 'stale', ready: false, indexed_chunks: 0 }))
    })
    expect(reactErrors.mock.calls.flat().join(' ')).not.toMatch(/useEffect must not return anything besides a function/i)
  })

  it('shows a recovery interface if a descendant render unexpectedly throws', () => {
    const errorLog = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    function BrokenAnswer() { throw new Error('render fixture'); return null }
    render(<ErrorBoundary><BrokenAnswer /></ErrorBoundary>)
    expect(screen.getByRole('alert').textContent).toContain('Chat view needs to restart')
    expect(screen.getByRole('button', { name: 'Restart chat' })).toBeTruthy()
    errorLog.mockRestore()
  })
})
