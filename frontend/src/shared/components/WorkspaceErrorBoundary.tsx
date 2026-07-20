import { Component, type ErrorInfo, type ReactNode } from "react";
import { Alert, Button } from "antd";
import api from "@shared/api/client";

interface Props { children: ReactNode }
interface State { error: Error | null }

export class WorkspaceErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("WorkspaceErrorBoundary caught:", error, errorInfo.componentStack);
    // Best-effort: an error boundary must never itself throw, so a failure
    // to report the crash (network down, expired session, etc.) is
    // swallowed rather than surfaced. Without this, a React crash was
    // previously visible only to whoever's browser it happened in.
    api
      .post("/client-errors", {
        message: error.message,
        stack: error.stack,
        component_stack: errorInfo.componentStack,
        url: window.location.href,
      })
      .catch(() => {});
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 24 }}>
          <Alert
            type="error"
            message="Workspace failed to render"
            description={this.state.error.message}
            action={
              <Button size="small" onClick={() => this.setState({ error: null })}>
                Retry
              </Button>
            }
          />
        </div>
      );
    }
    return this.props.children;
  }
}
