/**
 * Agent Hub Chat Interface (MVP)
 * Unified chat thread: humans + agents in one message stream
 * Collab Chat integration (Phase 3-4 exploration)
 *
 * @date 2026-09-25
 */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  Send,
  Loader2,
  MessageCircle,
  Clock,
  User,
  Bot,
  AlertCircle,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

// ────────────────────────────────────────────────────────────────────
// Types
// ────────────────────────────────────────────────────────────────────

interface ChatMessage {
  id: string;
  author_kind: "human" | "agent" | "system";
  author_name: string;
  text: string;
  timestamp: number;
  ai_generated?: boolean;
}

interface ChatThread {
  thread_id: string;
  title: string;
  message_count: number;
  created_at: number;
}

// ────────────────────────────────────────────────────────────────────
// MVP: Mock data (no backend yet, local state)
// ────────────────────────────────────────────────────────────────────

const MOCK_THREAD: ChatThread = {
  thread_id: "thread-001",
  title: "Agent Hub Chat",
  message_count: 0,
  created_at: Date.now(),
};

const MOCK_MESSAGES: ChatMessage[] = [
  {
    id: "msg-001",
    author_kind: "system",
    author_name: "System",
    text: "Welcome to Agent Hub Chat. Start a conversation or ask questions about agents.",
    timestamp: Date.now() - 60000,
  },
  {
    id: "msg-002",
    author_kind: "human",
    author_name: "You",
    text: "What agents are available?",
    timestamp: Date.now() - 45000,
  },
  {
    id: "msg-003",
    author_kind: "agent",
    author_name: "os-router",
    text: "I can classify requests and route them to the right executor. Available agents: os-router, os-executor, os-optimizer.",
    timestamp: Date.now() - 30000,
    ai_generated: true,
  },
];

// ────────────────────────────────────────────────────────────────────
// Components
// ────────────────────────────────────────────────────────────────────

interface ChatBubbleProps {
  message: ChatMessage;
}

function ChatBubble({ message }: ChatBubbleProps) {
  const isHuman = message.author_kind === "human";
  const isAgent = message.author_kind === "agent";
  const isSystem = message.author_kind === "system";

  return (
    <div
      className={cn(
        "flex gap-3 mb-4",
        isHuman ? "flex-row-reverse" : "flex-row"
      )}
    >
      {/* Avatar */}
      <div
        className={cn(
          "h-8 w-8 rounded-full flex items-center justify-center flex-none",
          isHuman
            ? "bg-blue-100 dark:bg-blue-900"
            : isAgent
              ? "bg-purple-100 dark:bg-purple-900"
              : "bg-gray-200 dark:bg-gray-700"
        )}
      >
        {isHuman && <User className="h-4 w-4 text-blue-600 dark:text-blue-300" />}
        {isAgent && <Bot className="h-4 w-4 text-purple-600 dark:text-purple-300" />}
        {isSystem && <MessageCircle className="h-4 w-4 text-gray-600 dark:text-gray-300" />}
      </div>

      {/* Message */}
      <div className="flex-1 max-w-md">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-sm font-medium">{message.author_name}</span>
          {isAgent && message.ai_generated && (
            <Badge variant="secondary" className="text-[10px]">
              AI
            </Badge>
          )}
          <span className="text-xs text-muted-foreground flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {new Date(message.timestamp).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            })}
          </span>
        </div>
        <div
          className={cn(
            "rounded-lg px-4 py-2 text-sm",
            isHuman
              ? "bg-blue-500 text-white rounded-br-none"
              : isAgent
                ? "bg-purple-100 dark:bg-purple-900 text-foreground rounded-bl-none"
                : "bg-gray-100 dark:bg-gray-800 text-foreground rounded-bl-none"
          )}
        >
          {message.text}
        </div>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// Main Component
// ────────────────────────────────────────────────────────────────────

export function AgentHubChat() {
  const [messages, setMessages] = React.useState<ChatMessage[]>(MOCK_MESSAGES);
  const [inputValue, setInputValue] = React.useState("");
  const [isLoading, setIsLoading] = React.useState(false);
  const scrollAreaRef = React.useRef<HTMLDivElement>(null);

  // Auto-scroll to latest message
  React.useEffect(() => {
    if (scrollAreaRef.current) {
      const scrollElement = scrollAreaRef.current.querySelector("[data-radix-scroll-area-viewport]");
      if (scrollElement) {
        setTimeout(() => {
          scrollElement.scrollTop = scrollElement.scrollHeight;
        }, 0);
      }
    }
  }, [messages]);

  const handleSendMessage = async () => {
    if (!inputValue.trim()) return;

    // Add user message
    const userMessage: ChatMessage = {
      id: `msg-${Date.now()}`,
      author_kind: "human",
      author_name: "You",
      text: inputValue,
      timestamp: Date.now(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInputValue("");
    setIsLoading(true);

    // Simulate agent response (MVP: mock response)
    setTimeout(() => {
      const agentMessage: ChatMessage = {
        id: `msg-${Date.now() + 1}`,
        author_kind: "agent",
        author_name: "os-router",
        text: `I received your message: "${inputValue}". This is a mock response. Full integration coming soon.`,
        timestamp: Date.now(),
        ai_generated: true,
      };
      setMessages((prev) => [...prev, agentMessage]);
      setIsLoading(false);
    }, 800);
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  return (
    <div className="flex flex-col h-full gap-4">
      {/* Header */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-lg">Agent Hub Chat (MVP)</CardTitle>
          <p className="text-sm text-muted-foreground">
            Unified chat interface for humans and agents
          </p>
        </CardHeader>
      </Card>

      {/* Chat Area */}
      <Card className="flex-1 flex flex-col">
        <CardContent className="flex-1 overflow-hidden flex flex-col p-4">
          {/* Messages */}
          <div
            className="flex-1 overflow-y-auto mb-4 pr-4"
            ref={scrollAreaRef}
          >
            <div className="space-y-2">
              {messages.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-64 text-muted-foreground">
                  <MessageCircle className="h-8 w-8 mb-2 opacity-50" />
                  <p className="text-sm">No messages yet. Start a conversation.</p>
                </div>
              ) : (
                messages.map((msg) => <ChatBubble key={msg.id} message={msg} />)
              )}
              {isLoading && (
                <div className="flex gap-2 items-center text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>Agent is thinking...</span>
                </div>
              )}
            </div>
          </div>

          {/* Input */}
          <div className="flex gap-2">
            <Input
              placeholder="Type a message... (Enter to send)"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyPress={handleKeyPress}
              disabled={isLoading}
              className="flex-1"
            />
            <Button
              onClick={handleSendMessage}
              disabled={!inputValue.trim() || isLoading}
              size="sm"
            >
              {isLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Status */}
      <div className="flex gap-2 items-center text-xs text-muted-foreground px-1">
        <AlertCircle className="h-3 w-3" />
        <span>MVP: Using mock data. Backend integration in Phase 2.</span>
      </div>
    </div>
  );
}
