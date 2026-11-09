// The memories screen renders the bridge's `memory.stats` frame, which wraps
// SoraAgent's `memory.doctor()` output plus on-disk sizes.

import { button, el, type ViewContext, type ViewModule } from "../app";
import { formatBytes, formatTimestamp } from "../format";

function card(label: string, value: string, hint?: string): HTMLElement {
  const node = el("div", "card");
  node.append(el("span", "card-label", label));
  node.append(el("span", "card-value", value));
  if (hint) node.append(el("span", "card-hint", hint));
  return node;
}

function yesNo(value: boolean | undefined): string {
  return value ? "yes" : "no";
}

export const memoriesView: ViewModule = {
  mount(root: HTMLElement, ctx: ViewContext): () => void {
    const view = el("div", "view");

    const head = el("header", "view-head");
    const titleWrap = el("div", "view-title");
    titleWrap.append(el("h2", undefined, "Memories"));
    titleWrap.append(el("span", "subtle", "SoraAgent memory health and storage"));
    const refreshBtn = button("Refresh", "btn ghost");
    head.append(titleWrap, refreshBtn);

    const body = el("div", "memories-body");
    view.append(head, body);
    root.append(view);

    const render = (): void => {
      const state = ctx.store.get();
      body.replaceChildren();
      if (state.status.state !== "connected") {
        body.append(el("p", "empty", "Connect to the desktop bridge to read memory stats."));
        return;
      }
      const memory = state.memory;
      if (!memory) {
        body.append(el("p", "empty", "No memory stats yet — press Refresh."));
        return;
      }

      const summary = el("div", "memories-summary");
      summary.append(
        el(
          "span",
          `badge ${memory.enabled ? "ok" : "muted"}`,
          memory.enabled ? "memory enabled" : "memory disabled",
        ),
      );
      if (memory.enabled) {
        summary.append(
          el(
            "span",
            `badge ${memory.config?.scheduler_running ? "ok" : "muted"}`,
            memory.config?.scheduler_running ? "dream scheduler running" : "dream scheduler idle",
          ),
        );
        summary.append(
          el(
            "span",
            `badge ${memory.sidecar?.healthy ? "ok" : "muted"}`,
            memory.sidecar?.healthy ? "sidecar healthy" : "sidecar idle",
          ),
        );
      }
      body.append(summary);

      if (!memory.enabled) {
        body.append(
          el(
            "p",
            "empty",
            "Memory is disabled in SoraAgent's configuration (features.memory.enabled).",
          ),
        );
        return;
      }

      const daily = memory.daily;
      const backup = memory.backup;
      const sidecar = memory.sidecar;
      const config = memory.config;

      const grid = el("div", "card-grid");
      grid.append(
        card(
          "Database",
          formatBytes(memory.db_bytes),
          memory.db_path || memory.root,
        ),
        card(
          "Daily entries",
          String(daily?.messages ?? 0),
          `${daily?.days ?? 0} day(s) · ${formatBytes(daily?.bytes ?? 0)}`,
        ),
        card(
          "Oldest daily",
          daily?.oldest ? formatTimestamp(daily.oldest) : "—",
          daily?.newest ? `newest ${formatTimestamp(daily.newest)}` : undefined,
        ),
        card(
          "Backups",
          String(backup?.count ?? 0),
          `${backup?.active ?? 0} active · ${backup?.daily ?? 0} daily · ${formatBytes(
            backup?.bytes ?? 0,
          )}`,
        ),
        card("Latest backup", backup?.latest || "—", backup?.latest ? "file name" : undefined),
        card(
          "Recall mode",
          config?.recall_mode || "—",
          config?.auto_dream ? "auto dream on" : "auto dream off",
        ),
        card(
          "Dream hour",
          config?.dream_hour_utc === undefined ? "—" : `${config.dream_hour_utc}:00 UTC`,
          config?.scheduler_running ? "scheduler running" : "scheduler idle",
        ),
        card("Memory root", memory.root || "—", "set by SoraAgent paths.memory_root"),
      );
      body.append(grid);

      const sidecarTitle = el("h3", undefined, "CortexDB sidecar");
      body.append(sidecarTitle);
      const sidecarGrid = el("div", "card-grid details-grid");
      sidecarGrid.append(
        card("Enabled", yesNo(sidecar?.enabled)),
        card("Binary", sidecar?.binary_present ? "present" : "missing", sidecar?.version),
        card("Running", yesNo(sidecar?.running), sidecar?.healthy ? "health check ok" : "not healthy"),
        card("HTTP", sidecar?.http_addr || "—"),
        card("gRPC", sidecar?.grpc_addr || "—"),
        card("Database", sidecar?.db_path || "—"),
        card("Backups", sidecar?.backup_dir || "—"),
        card("Log", sidecar?.log || "—"),
      );
      body.append(sidecarGrid);
    };

    refreshBtn.onclick = () => {
      void ctx.actions.refreshMemory();
    };

    const unsubscribe = ctx.store.subscribe(render);
    render();
    void ctx.actions.refreshMemory();

    return () => {
      unsubscribe();
    };
  },
};
