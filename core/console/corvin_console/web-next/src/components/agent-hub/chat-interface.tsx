/**
 * Agent Hub Chat Interface (Phase 1)
 * Extended with: Voice Summary (Opt-In), Full Media Support, Session Autonomy
 *
 * Architecture: Decoupled Voice (separate blueprint), Graceful Degradation
 * @date 2026-09-25
 * @phase Phase 1: Foundation
 */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Send,
  Loader2,
  MessageCircle,
  Clock,
  User,
  Bot,
  AlertCircle,
  Mic,
  MicOff,
  Users,
  Download,
  Code,
  Image as ImageIcon,
  Video as VideoIcon,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

// ────────────────────────────────────────────────────────────────────
// Types
// ────────────────────────────────────────────────────────────────────

type MediaType = "code" | "image" | "video" | "none";

interface MediaContent {
  type: MediaType;
  url: string;
  filename?: string;
  language?: string; // für code
  mimeType?: string;
}

interface ChatMessage {
  id: string;
  author_kind: "human" | "agent" | "system";
  author_name: string;
  text: string;
  timestamp: number;
  ai_generated?: boolean;
  media?: MediaContent;
}

interface ChatThread {
  thread_id: string;
  title: string;
  owner_id: string; // Session-Owner
  message_count: number;
  created_at: number;
  voice_summary?: string;
  is_voice_recording?: boolean;
  agent_invited?: boolean;
}

interface VoiceSummaryState {
  isRecording: boolean;
  isAvailable: boolean; // STT-Verfügbarkeit (Graceful Fallback)
  transcript?: string;
  summary?: string;
}

// ────────────────────────────────────────────────────────────────────
// Mock Data (Phase 1 Testing)
// ────────────────────────────────────────────────────────────────────

const MOCK_THREAD: ChatThread = {
  thread_id: "thread-001",
  title: "Agent Hub Chat",
  owner_id: "user-001", // User ist Owner
  message_count: 0,
  created_at: Date.now(),
  agent_invited: false,
};

const MOCK_MESSAGES: ChatMessage[] = [
  {
    id: "msg-001",
    author_kind: "system",
    author_name: "System",
    text: "Welcome to Extended Agent Hub Chat. Voice Summary (Opt-In), Full Media Support, User Sessions.",
    timestamp: Date.now() - 60000,
  },
  {
    id: "msg-002",
    author_kind: "human",
    author_name: "You",
    text: "Show me a code example",
    timestamp: Date.now() - 45000,
  },
  {
    id: "msg-003",
    author_kind: "agent",
    author_name: "os-router",
    text: "Here's a Python example that demonstrates request routing.",
    timestamp: Date.now() - 30000,
    ai_generated: true,
    media: {
      type: "code",
      language: "python",
      url: "https://example.com/code.py",
      filename: "router.py",
    },
  },
];

// ────────────────────────────────────────────────────────────────────
// Components
// ────────────────────────────────────────────────────────────────────

interface ChatBubbleProps {
  message: ChatMessage;
}

function MediaRenderer({ media }: { media: MediaContent }) {
  if (!media) return null;

  switch (media.type) {
    case "code":
      return (
        <div className="bg-gray-900 text-gray-100 rounded-lg p-3 font-mono text-xs max-w-2xl">
          <div className="flex items-center justify-between mb-2">
            <span className="text-gray-400">{media.language || "code"}</span>
            <a
              href={media.url}
              className="text-blue-400 hover:text-blue-300 flex items-center gap-1"
              download
            >
              <Download className="h-3 w-3" /> {media.filename}
            </a>
          </div>
          <pre className="overflow-x-auto">
            <code>
              {`def route_request(task):\n    if task.type == "complex":\n        return dispatch(opus)\n    return dispatch(haiku)`}
            </code>
          </pre>
        </div>
      );

    case "image":
      return (
        <div className="max-w-2xl">
          <img
            src={media.url}
            alt={media.filename || "Media"}
            className="rounded-lg max-h-64 object-cover"
          />
          {media.filename && (
            <p className="text-xs text-muted-foreground mt-1">{media.filename}</p>
          )}
        </div>
      );

    case "video":
      return (
        <div className="max-w-2xl">
          <video
            src={media.url}
            controls
            className="rounded-lg max-h-64 w-full"
          />
          {media.filename && (
            <p className="text-xs text-muted-foreground mt-1">{media.filename}</p>
          )}
        </div>
      );

    default:
      return null;
  }
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

      {/* Message Container */}
      <div className="flex-1 max-w-3xl">
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

        {/* Text Message */}
        <div
          className={cn(
            "rounded-lg px-4 py-2 text-sm mb-2",
            isHuman
              ? "bg-blue-500 text-white rounded-br-none"
              : isAgent
                ? "bg-purple-100 dark:bg-purple-900 text-foreground rounded-bl-none"
                : "bg-gray-100 dark:bg-gray-800 text-foreground rounded-bl-none"
          )}
        >
          {message.text}
        </div>

        {/* Media (Phase 1: Code, Images, Videos) */}
        {message.media && <MediaRenderer media={message.media} />}
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// Main Component
// ────────────────────────────────────────────────────────────────────

export function AgentHubChat() {
  const [thread, setThread] = React.useState<ChatThread>(MOCK_THREAD);
  const [messages, setMessages] = React.useState<ChatMessage[]>(MOCK_MESSAGES);
  const [inputValue, setInputValue] = React.useState("");
  const [isLoading, setIsLoading] = React.useState(false);
  const [voiceState, setVoiceState] = React.useState<VoiceSummaryState>({
    isRecording: false,
    isAvailable: true, // Mock: STT verfügbar
  });
  const scrollAreaRef = React.useRef<HTMLDivElement>(null);

  // Auto-scroll to latest message
  React.useEffect(() => {
    if (scrollAreaRef.current) {
      setTimeout(() => {
        scrollAreaRef.current!.scrollTop = scrollAreaRef.current!.scrollHeight;
      }, 0);
    }
  }, [messages]);

  const handleSendMessage = async () => {
    if (!inputValue.trim()) return;

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

    // Simulate agent response
    setTimeout(() => {
      const agentMessage: ChatMessage = {
        id: `msg-${Date.now() + 1}`,
        author_kind: "agent",
        author_name: "os-router",
        text: `I received: "${inputValue}". Phase 1 implementation with Voice Summary (Opt-In), Media Support, and Session Autonomy.`,
        timestamp: Date.now(),
        ai_generated: true,
      };
      setMessages((prev) => [...prev, agentMessage]);
      setIsLoading(false);
    }, 800);
  };

  const handleStartVoiceRecording = () => {
    if (!voiceState.isAvailable) {
      console.warn("STT not available - graceful fallback");
      return;
    }
    setVoiceState((prev) => ({
      ...prev,
      isRecording: !prev.isRecording,
    }));
  };

  const handleInviteAgent = () => {
    setThread((prev) => ({
      ...prev,
      agent_invited: !prev.agent_invited,
    }));
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  return (
    <div className="flex flex-col h-full gap-4">
      {/* Header + Session Controls */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-lg">{thread.title}</CardTitle>
              <p className="text-sm text-muted-foreground">
                Owner: <strong>{thread.owner_id}</strong> (User-Owned Session)
              </p>
            </div>
            <div className="flex gap-2">
              {/* Voice Summary Button (Opt-In) */}
              <Button
                size="sm"
                variant={voiceState.isRecording ? "default" : "outline"}
                onClick={handleStartVoiceRecording}
                disabled={!voiceState.isAvailable}
                className="gap-1"
              >
                {voiceState.isRecording ? (
                  <>
                    <Mic className="h-4 w-4 animate-pulse" />
                    Recording...
                  </>
                ) : (
                  <>
                    <MicOff className="h-4 w-4" />
                    Voice (Opt-In)
                  </>
                )}
              </Button>

              {/* Agent Invitation Button (Optional) */}
              <Button
                size="sm"
                variant={thread.agent_invited ? "default" : "outline"}
                onClick={handleInviteAgent}
                className="gap-1"
              >
                <Users className="h-4 w-4" />
                {thread.agent_invited ? "Agent Invited" : "Invite Agent"}
              </Button>
            </div>
          </div>

          {/* Voice Recording Status */}
          {voiceState.isRecording && (
            <div className="flex gap-2 items-center text-xs text-orange-600 dark:text-orange-400 mt-3 pt-3 border-t">
              <AlertCircle className="h-3 w-3" />
              Voice Summary is recording. Will auto-generate at session end.
            </div>
          )}

          {thread.agent_invited && (
            <div className="flex gap-2 items-center text-xs text-blue-600 dark:text-blue-400 mt-3 pt-3 border-t">
              <Users className="h-3 w-3" />
              Agent invited. Tasks tracked separately. User remains session owner.
            </div>
          )}
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
      <div className="flex gap-2 items-start text-xs text-muted-foreground px-1">
        <AlertCircle className="h-3 w-3 mt-0.5 flex-none" />
        <div className="space-y-1">
          <p>✅ Phase 1: Voice Summary (Opt-In) + Full Media Support (Code/Images/Videos)</p>
          <p>✅ Session-Owner model (User owns session, Agent optional)</p>
          <p>✅ Graceful Degradation: Chat works even without STT</p>
          <p>🟡 Backend: Voice routes + Audio processing coming next</p>
        </div>
      </div>
    </div>
  );
}
