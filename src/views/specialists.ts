// Persistent specialists (delegation agents) from SoraAgent's registry.
// The main agent invokes them through find_delegates/delegate_to_agent; this
// screen is for the user to inspect, pause, promote, or delete them.

import { button, el, type ViewContext, type ViewModule } from "../app";

export const specialistsView: ViewModule = {
  mount(root: HTMLElement, ctx: ViewContext): () => void {
    const view = el("div", "view");

    const head = el("header", "view-head");
    const titleWrap = el("div", "view-title");
    titleWrap.append(el("h2", undefined, "Specialists"));
    titleWrap.append(el("span", "subtle", "persistent delegation agents"));
    const refreshBtn = button("Refresh", "btn ghost");
    head.append(titleWrap, refreshBtn);

    const list = el("div", "list specialists-list");
    const hint = el("p", "subtle specialists-store");
    view.append(head, list, hint);
    root.append(view);

    const render = (): void => {
      const state = ctx.store.get();
      list.replaceChildren();
      hint.textContent = state.specialistsStore
        ? `Store: ${state.specialistsStore}`
        : "";
      if (state.status.state !== "connected") {
        list.append(el("p", "empty", "Connect to the desktop bridge to list specialists."));
        return;
      }
      if (state.specialists.length === 0) {
        list.append(
          el(
            "p",
            "empty",
            "No specialists yet. Create one with `sora delegate create` in a terminal.",
          ),
        );
        return;
      }
      for (const specialist of state.specialists) {
        const row = el("div", "list-row specialist-row");
        const main = el("div", "list-row-main");
        const titleLine = el("div", "specialist-title");
        titleLine.append(el("span", "list-title", specialist.name));
        titleLine.append(
          el(
            "span",
            `badge ${specialist.status === "active" ? "ok" : "muted"}`,
            specialist.status,
          ),
        );
        if (specialist.provisional) {
          titleLine.append(el("span", "badge mode-local", "provisional"));
        }
        main.append(titleLine);
        main.append(el("span", "subtle", `@${specialist.id}`));
        if (specialist.description) {
          main.append(el("span", "subtle", specialist.description));
        }
        if (specialist.when_to_use) {
          main.append(el("span", "subtle", `Use when: ${specialist.when_to_use}`));
        }
        const model = [specialist.provider, specialist.model].filter(Boolean).join("/");
        main.append(
          el(
            "span",
            "subtle",
            `${specialist.tools.join(", ") || "no tools"} · ${model || "main model"} · created by ${specialist.created_by}`,
          ),
        );

        const actions = el("div", "specialist-actions");
        const toggle = button(
          specialist.status === "active" ? "Pause" : "Resume",
          "btn ghost small",
        );
        toggle.onclick = () => {
          const next = specialist.status === "active" ? "paused" : "active";
          void ctx.actions
            .setSpecialistStatus(specialist.id, next)
            .catch((error: unknown) => ctx.actions.toast(String(error), "error"));
        };
        actions.append(toggle);
        if (specialist.provisional) {
          const promote = button("Promote", "btn ghost small");
          promote.onclick = () => {
            void ctx.actions
              .promoteSpecialist(specialist.id)
              .catch((error: unknown) => ctx.actions.toast(String(error), "error"));
          };
          actions.append(promote);
        }
        const remove = button("Delete", "btn ghost small danger");
        remove.onclick = () => {
          if (!window.confirm(`Delete specialist ${specialist.name}?`)) return;
          void ctx.actions
            .deleteSpecialist(specialist.id)
            .catch((error: unknown) => ctx.actions.toast(String(error), "error"));
        };
        actions.append(remove);
        row.append(main, actions);
        list.append(row);
      }
    };

    refreshBtn.onclick = () => {
      void ctx.actions.refreshSpecialists();
    };

    const unsubscribe = ctx.store.subscribe(render);
    render();
    void ctx.actions.refreshSpecialists();

    return () => {
      unsubscribe();
    };
  },
};
