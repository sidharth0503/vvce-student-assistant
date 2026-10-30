import { Component, type ErrorInfo, type ReactNode } from 'react'

type Props = { children: ReactNode }
type State = { failed: boolean; resetKey: number }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false, resetKey: 0 }

  static getDerivedStateFromError(): State {
    return { failed: true, resetKey: 0 }
  }

  componentDidCatch(_error: Error, _info: ErrorInfo) {
    console.error('VVCE chat view recovered from an unexpected render error.')
  }

  render() {
    if (this.state.failed) {
      return <main className="app-shell" role="alert">
        <section className="readiness-note" style={{ margin: 'auto', maxWidth: 480, padding: 24 }}>
          <div>
            <h1>Chat view needs to restart</h1>
            <p>The conversation could not be displayed. Restart the chat view to continue.</p>
            <button className="new-chat" onClick={() => this.setState(state => ({ failed: false, resetKey: state.resetKey + 1 }))}>Restart chat</button>
          </div>
        </section>
      </main>
    }
    return <div key={this.state.resetKey} className="error-boundary-content">{this.props.children}</div>
  }
}
