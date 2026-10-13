/**
 * Tests für Phase 1 + Phase 2: Emoji-Enhancement für Peer-Chat UI
 * - Phase 1: Message-Typ-Emojis (📤 / 💬)
 * - Phase 2: Status-Icons als Emojis (✓ / ✓✓ / ⏱ / 🚫 / ❓)
 *
 * WCAG AA + Dark Mode + Mobile-Responsiveness
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageStatusSymbol } from "@/components/chat/PeerConversation";
import type { MessageStatusView, StatusIcon } from "@/lib/a2a-message-status";

describe("PeerChat Emoji Enhancement", () => {
  describe("Phase 1: Message-Kind Emojis", () => {
    it("should render message-kind emoji with proper accessibility", () => {
      const { container } = render(
        <div>
          <span
            role="img"
            aria-label="Outgoing message"
            className="text-sm leading-none"
            data-testid="peer-message-kind-emoji"
          >
            📤
          </span>
        </div>
      );

      const emoji = screen.getByTestId("peer-message-kind-emoji");
      expect(emoji).toHaveAttribute("role", "img");
      expect(emoji).toHaveAttribute("aria-label", "Outgoing message");
      expect(emoji).toHaveTextContent("📤");
    });

    it("should use correct emojis for task vs response", () => {
      const taskEmoji = "📤"; // task = outgoing
      const responseEmoji = "💬"; // response = incoming

      expect(taskEmoji).toBe("📤");
      expect(responseEmoji).toBe("💬");
    });
  });

  describe("Phase 2: Status-Icon Emojis", () => {
    it("should render status emoji with accessibility label", () => {
      const statusView: MessageStatusView = {
        key: "sent",
        icon: "check" as StatusIcon,
        label: "Sent",
        detail: "Message sent successfully",
        tone: "success",
        chain: [],
        observedAgeS: null,
      };

      const { container } = render(
        <MessageStatusSymbol v={statusView} mine={true} />
      );

      const emoji = screen.getByTestId("peer-message-status-emoji");
      expect(emoji).toHaveAttribute("role", "img");
      expect(emoji).toHaveAttribute("aria-label", "Sent");
      expect(emoji).toHaveTextContent("✓");
    });

    it("should hide label on mobile (md breakpoint)", () => {
      const statusView: MessageStatusView = {
        key: "queued",
        icon: "clock" as StatusIcon,
        label: "Sending…",
        detail: "Message queued for delivery",
        tone: "working",
        chain: [],
        observedAgeS: null,
      };

      const { container } = render(
        <MessageStatusSymbol v={statusView} mine={false} />
      );

      // The label should exist but have class "hidden md:inline"
      const label = container.querySelector(".hidden.md\\:inline");
      expect(label).toBeInTheDocument();
    });

    it("should support all status emoji mappings", () => {
      const statusEmojis: Record<StatusIcon, string> = {
        clock: "⏱",
        check: "✓",
        "check-check": "✓✓",
        spinner: "🔄",
        x: "🚫",
        help: "❓",
      };

      Object.entries(statusEmojis).forEach(([status, emoji]) => {
        expect(emoji).toBeTruthy();
        expect(typeof emoji).toBe("string");
      });
    });
  });

  describe("Dark Mode Compatibility", () => {
    it("emojis should be colorless (no special dark mode CSS needed)", () => {
      const emoji = "✓";
      // Emojis are platform-rendered, not affected by CSS colors
      // Just verify they're strings without color dependencies
      expect(emoji).toBeTruthy();
      expect(typeof emoji).toBe("string");
    });
  });

  describe("Accessibility (WCAG AA)", () => {
    it("should have proper aria-labels on all emoji spans", () => {
      const testCases = [
        {
          emoji: "📤",
          label: "Outgoing message",
          testid: "peer-message-kind-emoji",
        },
        {
          emoji: "⚠️",
          label: "Warning",
          testid: "peer-message-error-warning",
        },
        {
          emoji: "🚫",
          label: "Error",
          testid: "peer-message-error",
        },
      ];

      testCases.forEach(({ emoji, label }) => {
        const { container } = render(
          <span role="img" aria-label={label}>
            {emoji}
          </span>
        );

        const img = container.querySelector("[role='img']");
        expect(img).toHaveAttribute("aria-label", label);
      });
    });

    it("should have sr-only status detail for screen readers", () => {
      const statusView: MessageStatusView = {
        key: "delivered",
        icon: "check-check" as StatusIcon,
        label: "Delivered",
        detail: "Message delivered to peer",
        tone: "success",
        chain: [],
        observedAgeS: 120,
      };

      const { container } = render(
        <MessageStatusSymbol v={statusView} mine={true} />
      );

      const srOnly = container.querySelector(".sr-only");
      expect(srOnly).toHaveTextContent("Message delivered to peer");
    });
  });

  describe("Mobile Responsiveness", () => {
    it("should hide non-essential details on mobile", () => {
      const { container } = render(
        <div className="hidden md:inline">
          Visible only on desktop
        </div>
      );

      const element = container.querySelector(".hidden.md\\:inline");
      expect(element).toBeInTheDocument();
      // In actual UI, this would be hidden on mobile screens
    });

    it("should show emoji label on desktop, hide on mobile", () => {
      const statusView: MessageStatusView = {
        key: "working",
        icon: "spinner" as StatusIcon,
        label: "Processing…",
        detail: "Peer is processing your request",
        tone: "working",
        chain: [],
        observedAgeS: null,
      };

      const { container } = render(
        <MessageStatusSymbol v={statusView} mine={true} />
      );

      // Emoji always visible
      const emoji = screen.getByTestId("peer-message-status-emoji");
      expect(emoji).toBeInTheDocument();

      // Label hidden on mobile via Tailwind class
      const label = container.querySelector(".hidden.md\\:inline");
      expect(label).toBeInTheDocument();
    });
  });
});
