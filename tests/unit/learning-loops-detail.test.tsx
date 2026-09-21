import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LearningLoopDetail } from "@/components/learning-loop-detail";

const mockLoop = {
  loop_id: "loop-1",
  plugin_id: "plugin-a",
  status: "active" as const,
  health: { score: 0.95, trend: "up" as const },
  last_event: new Date().toISOString(),
  event_count_7d: 42,
  event_count_30d: 120,
  description: "Test loop description",
};

jest.mock("@/hooks/use-learning-loops-detail", () => ({
  useDetail: () => ({
    trend: {
      points: [
        { date: "2026-01-15", health_score: 0.9, event_count: 5 },
        { date: "2026-01-16", health_score: 0.92, event_count: 6 },
      ],
      min_score: 0.9,
      max_score: 0.95,
      avg_score: 0.92,
    },
    events: [
      { timestamp: new Date().toISOString(), event_type: "outcome_feedback", outcome: "success" },
      { timestamp: new Date(Date.now() - 3600000).toISOString(), event_type: "skill_executed", signal: "high_confidence" },
    ],
    loading: false,
  }),
}));

describe("LearningLoopDetail", () => {
  test("renders loop details", () => {
    render(<LearningLoopDetail loop={mockLoop} />);
    expect(screen.getByText("plugin-a")).toBeInTheDocument();
    expect(screen.getByText("loop-1")).toBeInTheDocument();
  });

  test("displays health metrics", () => {
    render(<LearningLoopDetail loop={mockLoop} />);
    expect(screen.getByText("95%")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("120")).toBeInTheDocument();
  });

  test("shows status badge", () => {
    render(<LearningLoopDetail loop={mockLoop} />);
    expect(screen.getByText("active")).toBeInTheDocument();
  });

  test("renders trend tab content", async () => {
    const user = userEvent.setup();
    render(<LearningLoopDetail loop={mockLoop} />);
    const trendTab = screen.getByRole("tab", { name: /trend/i });
    await user.click(trendTab);
    expect(screen.getByText("Min")).toBeInTheDocument();
    expect(screen.getByText("Avg")).toBeInTheDocument();
    expect(screen.getByText("Max")).toBeInTheDocument();
  });

  test("renders events tab with data", async () => {
    const user = userEvent.setup();
    render(<LearningLoopDetail loop={mockLoop} />);
    const eventsTab = screen.getByRole("tab", { name: /events/i });
    await user.click(eventsTab);
    expect(screen.getByText("outcome_feedback")).toBeInTheDocument();
    expect(screen.getByText("skill_executed")).toBeInTheDocument();
  });

  test("export button is present and clickable", async () => {
    const user = userEvent.setup();
    render(<LearningLoopDetail loop={mockLoop} />);
    const exportBtn = screen.getByRole("button", { name: /export/i });
    expect(exportBtn).toBeInTheDocument();
    await user.click(exportBtn);
  });

  test("displays description when present", () => {
    render(<LearningLoopDetail loop={mockLoop} />);
    expect(screen.getByText("Test loop description")).toBeInTheDocument();
  });

  test("shows trend bars in sparkline", async () => {
    const user = userEvent.setup();
    render(<LearningLoopDetail loop={mockLoop} />);
    const trendTab = screen.getByRole("tab", { name: /trend/i });
    await user.click(trendTab);
    const trendBars = screen.getByTitle(/2026-01-15/);
    expect(trendBars).toBeInTheDocument();
  });

  test("renders status badge color correctly", () => {
    const { container } = render(<LearningLoopDetail loop={mockLoop} />);
    const badge = container.querySelector('[class*="bg-green"]');
    expect(badge).toBeInTheDocument();
  });
});
