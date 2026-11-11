/** "New item" on a project's board follows the project's knowledge base (REVIEW-0083 N32, REVIEW-0084 N33/N34). */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
afterEach(cleanup);
import { CreateDialog } from "@/pages/tasks/create-dialog";
import type { Item } from "@/lib/api/task-tracking";

const it_ = (id: string, kind: string, title: string) => ({ id, kind, title, deleted_at: null }) as unknown as Item;
const ITEMS = [it_("i1", "initiative", "I-01 · Init"), it_("e1", "epic", "E-001 · Epic"), it_("t1", "task", "T-0001 · Task")];

function open(props: Partial<Parameters<typeof CreateDialog>[0]> = {}) {
  const onCreate = vi.fn();
  render(<CreateDialog open onOpenChange={() => {}} items={ITEMS} defaultParent={null} busy={false} error={null}
    onCreate={onCreate} project="Alpha" {...props} />);
  return onCreate;
}

describe("the create dialog on a project's board", () => {
  it("offers a task only an epic as its parent", () => {
    open();
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "task" } });
    const parents = Array.from((screen.getByLabelText("Parent") as HTMLSelectElement).options).map((o) => o.textContent);
    expect(parents).toEqual(["Choose a parent…", "Epic: E-001 · Epic"]);
  });

  it("Add child on a task says the knowledge base has none, and creates nothing", () => {
    const onCreate = open({ defaultParent: ITEMS[2] });
    expect(screen.getByTestId("create-no-children").textContent).toContain("has no children");
    expect(screen.queryByLabelText("Kind")).toBeNull();
    expect(onCreate).not.toHaveBeenCalled();
  });

  it("Add child on an epic opens a task under it", () => {
    open({ defaultParent: ITEMS[1] });
    expect((screen.getByLabelText("Kind") as HTMLSelectElement).value).toBe("task");
    expect((screen.getByLabelText("Parent") as HTMLSelectElement).value).toBe("e1");
  });
});

describe("on any board, the parent decides (REVIEW-0085 N36, N37)", () => {
  const card = (id: string, kind: string, title: string, ref: string | null, labels: string[] = []) =>
    ({ id, kind, title, deleted_at: null, external_ref: ref, labels }) as unknown as Item;
  const MIXED = [
    card("bi", "initiative", "I-01 · bound", "kb:U1"), card("be", "epic", "E-001 · bound", "kb:U2"),
    card("mi", "initiative", "manual initiative", null),
    card("at", "task", "T-0001 · alpha task", "kb:U3@alpha"), card("lt", "task", "T-0002 · legacy", "kb:U4", ["kb", "prj-t"]),
  ];
  const openAll = (defaultParent: Item | null = null) => render(<CreateDialog open onOpenChange={() => {}} items={MIXED}
    defaultParent={defaultParent} busy={false} error={null} onCreate={vi.fn()} />);

  it("a task is offered a KB epic or a manual initiative, never a KB initiative", () => {
    openAll();
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "task" } });
    const parents = Array.from((screen.getByLabelText("Parent") as HTMLSelectElement).options).map((o) => o.value);
    expect(parents).toContain("be");
    expect(parents).toContain("mi");
    expect(parents).not.toContain("bi");
  });

  it("Add child on a project's task from the All view says it has none - new and legacy references alike", () => {
    openAll(MIXED[3]);
    expect(screen.getByTestId("create-no-children").textContent).toContain("alpha's knowledge base");
    cleanup();
    openAll(MIXED[4]);
    expect(screen.getByTestId("create-no-children").textContent).toContain("t's knowledge base");
  });
});
