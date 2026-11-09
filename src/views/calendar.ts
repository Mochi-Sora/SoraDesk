// The calendar screen renders SoraAgent's local events and to-dos. It is
// read-only: the agent owns the calendar, the client displays it.

import { button, el, type ViewContext, type ViewModule } from "../app";
import { formatTimestamp } from "../format";
import type { CalendarItem } from "../protocol";

function kindBadge(kind: string): HTMLElement {
  return el("span", `badge ${kind === "event" ? "mode-local" : "mode-ssh"}`, kind);
}

function itemRow(item: CalendarItem): HTMLElement {
  const row = el("div", "list-row profile-row");
  const main = el("div", "list-row-main");
  const line = el("div", "profile-line");
  line.append(el("span", "list-title", item.title), kindBadge(item.kind));
  if (item.importance === "important") line.append(el("span", "badge err", "important"));
  if (item.done) line.append(el("span", "badge muted", "done"));
  main.append(line);

  if (item.kind === "event" && item.starts_at) {
    const ending = item.ends_at ? ` → ${formatTimestamp(item.ends_at)}` : "";
    main.append(el("span", "subtle", `${formatTimestamp(item.starts_at)}${ending}`));
  } else if (item.due_at) {
    main.append(el("span", "subtle", `due ${formatTimestamp(item.due_at)}`));
  }

  if (item.notes) main.append(el("span", "subtle", item.notes));
  row.append(main);
  return row;
}

function section(title: string, items: CalendarItem[]): HTMLElement | null {
  if (items.length === 0) return null;
  const wrap = el("section", "section");
  wrap.append(el("h3", undefined, title));
  const list = el("div", "list");
  for (const item of items) list.append(itemRow(item));
  wrap.append(list);
  return wrap;
}

export const calendarView: ViewModule = {
  mount(root: HTMLElement, ctx: ViewContext): () => void {
    const view = el("div", "view");

    const head = el("header", "view-head");
    const titleWrap = el("div", "view-title");
    titleWrap.append(el("h2", undefined, "Calendar"));
    titleWrap.append(el("span", "subtle", "events and to-dos from SoraAgent's local calendar"));
    const refreshBtn = button("Refresh", "btn ghost");
    head.append(titleWrap, refreshBtn);

    const body = el("div", "memories-body");
    view.append(head, body);
    root.append(view);

    const render = (): void => {
      const state = ctx.store.get();
      body.replaceChildren();
      if (state.status.state !== "connected") {
        body.append(el("p", "empty", "Connect to the desktop bridge to read the calendar."));
        return;
      }
      if (state.calendar.length === 0) {
        body.append(
          el(
            "p",
            "empty",
            "No events in the next 30 days and no to-dos. Ask Sora to add one from the chat.",
          ),
        );
        return;
      }

      const events = state.calendar.filter((item) => item.kind === "event");
      const todos = state.calendar.filter((item) => item.kind !== "event");
      const groups = [section("Events", events), section("To-dos", todos)];
      for (const group of groups) {
        if (group) body.append(group);
      }
      body.append(
        el(
          "p",
          "subtle footnote",
          "Read-only view of the calendar SQLite store owned by SoraAgent.",
        ),
      );
    };

    refreshBtn.onclick = () => {
      void ctx.actions.refreshCalendar();
    };

    const unsubscribe = ctx.store.subscribe(render);
    render();
    void ctx.actions.refreshCalendar();

    return () => {
      unsubscribe();
    };
  },
};
