// Author: @SaadZarook
import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  failed: boolean;
}

/** Wraps a page so a render failure shows a message and a retry, not a blank screen. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Page failed to render", error.message, info.componentStack);
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="error-state" role="alert">
          <p>This page could not be shown.</p>
          <button className="btn" type="button" onClick={() => this.setState({ failed: false })}>
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
