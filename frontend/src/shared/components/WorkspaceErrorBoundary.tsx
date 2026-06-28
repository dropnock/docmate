import { Component, type ReactNode } from "react";
import { Alert, Button } from "antd";

interface Props { children: ReactNode }
interface State { error: Error | null }

export class WorkspaceErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
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
