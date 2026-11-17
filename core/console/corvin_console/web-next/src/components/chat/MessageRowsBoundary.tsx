import * as React from "react";

interface Props {
  /** Rendered instead of the children after they threw while rendering. */
  fallback: React.ReactNode;
  /** A change of this value (the chat id) gives the children another try. */
  resetKey: string;
  children: React.ReactNode;
}

/**
 * Keeps a failure of the chat's message list inside the list.
 *
 * The route error boundary replaces the WHOLE page with "Something went wrong". The message
 * list is the one part of the chat that does heavy layout work (windowing, measuring), so a
 * fault there must not take the composer, the header and the running turn with it: the list
 * degrades to a plain, unwindowed tail and the rest of the chat keeps working. The failure is
 * still logged (never swallowed); the content is not.
 */
export class MessageRowsBoundary extends React.Component<Props, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: Error): void {
    console.error("[MessageRowsBoundary] message list failed, showing the plain list:", error.message);
  }

  componentDidUpdate(prev: Props): void {
    if (this.state.failed && prev.resetKey !== this.props.resetKey) this.setState({ failed: false });
  }

  render(): React.ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
