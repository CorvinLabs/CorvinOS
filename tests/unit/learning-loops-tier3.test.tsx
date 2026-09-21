import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LearningLoopAuditLog } from "@/components/learning-loop-audit-log";
import { getStatusColor, getHealthColor, learningLoopsColors } from "@/theme/learning-loops-colors";

const mockEvents = [
  { timestamp: new Date().toISOString(), event_type: "skill_executed", outcome: "success", skill_id: "skill-1" },
  { timestamp: new Date(Date.now() - 3600000).toISOString(), event_type: "outcome_feedback", signal: "high_confidence" },
  { timestamp: new Date(Date.now() - 7200000).toISOString(), event_type: "learning_event", outcome: "failure" },
];

describe("LearningLoopAuditLog", () => {
  test("renders audit log with events", () => {
    render(<LearningLoopAuditLog events={mockEvents} loopId="loop-1" />);
    expect(screen.getByText("skill_executed")).toBeInTheDocument();
    expect(screen.getByText("outcome_feedback")).toBeInTheDocument();
  });

  test("filters events by type", async () => {
    const user = userEvent.setup();
    render(<LearningLoopAuditLog events={mockEvents} loopId="loop-1" />);
    const skillBadge = screen.getByText(/skill_executed/);
    await user.click(skillBadge);
    expect(screen.getByText("skill_executed")).toBeInTheDocument();
    expect(screen.queryByText("outcome_feedback")).not.toBeInTheDocument();
  });

  test("text search filters events", async () => {
    const user = userEvent.setup();
    render(<LearningLoopAuditLog events={mockEvents} loopId="loop-1" />);
    const input = screen.getByPlaceholderText(/filter by event type/i);
    await user.type(input, "outcome");
    expect(screen.getByText("outcome_feedback")).toBeInTheDocument();
    expect(screen.queryByText("skill_executed")).not.toBeInTheDocument();
  });

  test("export to JSON button works", async () => {
    const user = userEvent.setup();
    const createObjectURLMock = jest.fn(() => "blob://mock-url");
    global.URL.createObjectURL = createObjectURLMock;

    render(<LearningLoopAuditLog events={mockEvents} loopId="loop-1" />);
    const jsonBtn = screen.getByText("JSON");
    await user.click(jsonBtn);
    expect(createObjectURLMock).toHaveBeenCalled();
  });

  test("export to CSV button works", async () => {
    const user = userEvent.setup();
    const createObjectURLMock = jest.fn(() => "blob://mock-url");
    global.URL.createObjectURL = createObjectURLMock;

    render(<LearningLoopAuditLog events={mockEvents} loopId="loop-1" />);
    const csvBtn = screen.getByText("CSV");
    await user.click(csvBtn);
    expect(createObjectURLMock).toHaveBeenCalled();
  });

  test("shows outcome badges", () => {
    render(<LearningLoopAuditLog events={mockEvents} loopId="loop-1" />);
    expect(screen.getByText("success")).toBeInTheDocument();
    expect(screen.getByText("failure")).toBeInTheDocument();
  });

  test("displays event type counts", () => {
    render(<LearningLoopAuditLog events={mockEvents} loopId="loop-1" />);
    expect(screen.getByText(/All \(3\)/)).toBeInTheDocument();
    expect(screen.getByText(/skill_executed \(1\)/)).toBeInTheDocument();
  });

  test("empty state when no events match", async () => {
    const user = userEvent.setup();
    render(<LearningLoopAuditLog events={mockEvents} loopId="loop-1" />);
    const input = screen.getByPlaceholderText(/filter by event type/i);
    await user.type(input, "nonexistent");
    expect(screen.getByText(/no events match filter/i)).toBeInTheDocument();
  });
});

describe("Learning Loops Colors (Dark Mode)", () => {
  test("light mode status colors defined", () => {
    const colors = learningLoopsColors.light.status;
    expect(colors.active).toBeDefined();
    expect(colors.dormant).toBeDefined();
    expect(colors.stale).toBeDefined();
    expect(colors.degrading).toBeDefined();
  });

  test("dark mode status colors defined", () => {
    const colors = learningLoopsColors.dark.status;
    expect(colors.active).toBeDefined();
    expect(colors.dormant).toBeDefined();
    expect(colors.stale).toBeDefined();
    expect(colors.degrading).toBeDefined();
  });

  test("getStatusColor returns correct light colors", () => {
    const color = getStatusColor("active", false);
    expect(color).toContain("green");
  });

  test("getStatusColor returns correct dark colors", () => {
    const color = getStatusColor("active", true);
    expect(color).toContain("green");
  });

  test("getHealthColor good score", () => {
    const color = getHealthColor(0.9, false);
    expect(color).toContain("emerald");
  });

  test("getHealthColor fair score", () => {
    const color = getHealthColor(0.5, false);
    expect(color).toContain("amber");
  });

  test("getHealthColor poor score", () => {
    const color = getHealthColor(0.2, false);
    expect(color).toContain("rose");
  });

  test("event type colors exist", () => {
    const lightColors = learningLoopsColors.light.eventTypes;
    const darkColors = learningLoopsColors.dark.eventTypes;
    expect(lightColors.outcome_feedback).toBeDefined();
    expect(darkColors.skill_executed).toBeDefined();
  });

  test("trend colors exist for all directions", () => {
    const lightTrend = learningLoopsColors.light.trend;
    expect(lightTrend.up).toBeDefined();
    expect(lightTrend.down).toBeDefined();
    expect(lightTrend.flat).toBeDefined();
  });
});

describe("WebSocket Updates (useWebSocketLoopUpdates)", () => {
  let mockWs: Partial<WebSocket>;
  const mockWebSocket = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    mockWs = {
      onopen: null,
      onmessage: null,
      onerror: null,
      onclose: null,
      close: jest.fn(),
    };
    mockWebSocket.mockReturnValue(mockWs);
    global.WebSocket = mockWebSocket as any;
  });

  test("WebSocket connects on loopId provided", () => {
    const { useWebSocketLoopUpdates } = require("@/hooks/use-websocket-loop-updates");
    const { renderHook } = require("@testing-library/react");
    const { result } = renderHook(() => useWebSocketLoopUpdates("loop-1"));
    expect(mockWebSocket).toHaveBeenCalled();
  });

  test("WebSocket ignores null loopId", () => {
    const { useWebSocketLoopUpdates } = require("@/hooks/use-websocket-loop-updates");
    const { renderHook } = require("@testing-library/react");
    renderHook(() => useWebSocketLoopUpdates(null));
    expect(mockWebSocket).not.toHaveBeenCalled();
  });

  test("WebSocket closes on unmount", () => {
    const { useWebSocketLoopUpdates } = require("@/hooks/use-websocket-loop-updates");
    const { renderHook } = require("@testing-library/react");
    const { unmount } = renderHook(() => useWebSocketLoopUpdates("loop-1"));
    unmount();
    expect(mockWs.close).toHaveBeenCalled();
  });
});
