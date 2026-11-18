import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  title: string;
  body: string;
  retryLabel: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/** Keeps a render error on one page from blanking the whole SPA. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[ibkr-dashboard] page render failed', error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="error-boundary" role="alert">
        <h4>{this.props.title}</h4>
        <p>{this.props.body}</p>
        <code>{this.state.error.message}</code>
        <button type="button" onClick={() => this.setState({ error: null })}>{this.props.retryLabel}</button>
      </div>
    );
  }
}
