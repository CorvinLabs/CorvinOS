import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LearningLoopGrid } from "@/components/learning-loop-grid";

const mockLoops = [
  {
    loop_id: "loop-1",
    plugin_id: "plugin-a",
    status: "active" as const,
    health: { score: 0.95, trend: "up" as const },
    last_event: new Date().toISOString(),
    event_count_7d: 42,
  },
  {
    loop_id: "loop-2",
    plugin_id: "plugin-b",
    status: "dormant" as const,
    health: { score: 0.5, trend: "down" as const },
    last_event: new Date(Date.now() - 3600000).toISOString(),
    event_count_7d: 10,
  },
];

describe("LearningLoopGrid", () => {
  test("renders grid with loops", () => {
    const onSelect = jest.fn();
    render(<LearningLoopGrid loops={mockLoops} loading={false} onSelect={onSelect} />);
    expect(screen.getByText("plugin-a")).toBeInTheDocument();
    expect(screen.getByText("plugin-b")).toBeInTheDocument();
  });

  test("displays loading state", () => {
    render(<LearningLoopGrid loops={[]} loading={true} onSelect={jest.fn()} />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  test("filters by status", async () => {
    const user = userEvent.setup();
    render(<LearningLoopGrid loops={mockLoops} loading={false} onSelect={jest.fn()} />);
    const activeBtn = screen.getByRole("button", { name: /active/i });
    await user.click(activeBtn);
    expect(screen.getByText("plugin-a")).toBeInTheDocument();
    expect(screen.queryByText("plugin-b")).not.toBeInTheDocument();
  });

  test("sorts by column click", async () => {
    const user = userEvent.setup();
    render(<LearningLoopGrid loops={mockLoops} loading={false} onSelect={jest.fn()} />);
    const pluginHeader = screen.getByText("Plugin");
    await user.click(pluginHeader);
    const rows = screen.getAllByRole("row");
    expect(within(rows[1]).getByText("plugin-a")).toBeInTheDocument();
  });

  test("calls onSelect when detail button clicked", async () => {
    const user = userEvent.setup();
    const onSelect = jest.fn();
    render(<LearningLoopGrid loops={mockLoops} loading={false} onSelect={onSelect} />);
    const detailButtons = screen.getAllByRole("button", { name: /chevron/i });
    await user.click(detailButtons[0]);
    expect(onSelect).toHaveBeenCalledWith("loop-1");
  });

  test("displays health bar correctly", () => {
    render(<LearningLoopGrid loops={mockLoops} loading={false} onSelect={jest.fn()} />);
    const healthBars = screen.getAllByRole("progressbar");
    expect(healthBars.length).toBeGreaterThan(0);
  });

  test("shows event count", () => {
    render(<LearningLoopGrid loops={mockLoops} loading={false} onSelect={jest.fn()} />);
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("10")).toBeInTheDocument();
  });

  test("displays status badges", () => {
    render(<LearningLoopGrid loops={mockLoops} loading={false} onSelect={jest.fn()} />);
    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.getByText("dormant")).toBeInTheDocument();
  });

  test("empty state message when no loops", () => {
    render(<LearningLoopGrid loops={[]} loading={false} onSelect={jest.fn()} />);
    expect(screen.getByText(/no learning loops available/i)).toBeInTheDocument();
  });
});
