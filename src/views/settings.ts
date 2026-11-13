// Settings: connection profiles, device tokens, the local desktop bridge (desktop only),
// and the browser dev backend switch.

import { button, el, type ViewContext, type ViewModule } from "../app";
import { open as openFileDialog } from "@tauri-apps/plugin-dialog";
import type { Update } from "@tauri-apps/plugin-updater";
import type { GatewayState } from "../connection/gateway";
import { isTauri, type ConnectionMode, type Profile } from "../connection/profiles";
import { errorText, formatTimestamp, truncate } from "../format";
import { APP_VERSION, PROTOCOL_VERSION } from "../protocol";

const MODES: ConnectionMode[] = ["local", "ssh", "ip"];

function modeBadge(mode: ConnectionMode): HTMLElement {
  return el("span", `badge mode-${mode}`, mode);
}

function maskToken(token: string): string {
  if (token.length <= 8) return "••••";
  return `••••••${token.slice(-4)}`;
}

export const settingsView: ViewModule = {
  mount(root: HTMLElement, ctx: ViewContext): () => void {
    const view = el("div", "view settings-view");
    const head = el("header", "view-head");
    const titleWrap = el("div", "view-title");
    titleWrap.append(el("h2", undefined, "Settings"));
    titleWrap.append(
      el("span", "subtle", "connections, device tokens, and the local desktop bridge"),
    );
    head.append(titleWrap);

    const scroll = el("div", "settings-scroll");
    view.append(head, scroll);
    root.append(view);

    // --- profiles form -------------------------------------------------------

    let editing: Profile | null = null;
    let formOpen = false;

    const form = el("form", "form hidden");
    const nameField = el("div", "field");
    const nameLabel = el("label", undefined, "Name");
    const nameInput = el("input");
    nameInput.type = "text";
    nameInput.placeholder = "My VPS";
    nameField.append(nameLabel, nameInput);

    const modeField = el("div", "field");
    modeField.append(el("label", undefined, "Mode"));
    const modeSelect = el("select");
    for (const mode of MODES) {
      const option = el("option", undefined, mode);
      option.value = mode;
      modeSelect.append(option);
    }
    modeField.append(modeSelect);

    const sshBlock = el("div", "form-block hidden");
    const sshHostField = el("div", "field");
    sshHostField.append(el("label", undefined, "SSH target (user@host)"));
    const sshHostInput = el("input");
    sshHostInput.type = "text";
    sshHostInput.placeholder = "ubuntu@203.0.113.10";
    sshHostField.append(
      sshHostInput,
      el("span", "field-hint", "The same target you would pass to ssh."),
    );

    const sshPortField = el("div", "field");
    sshPortField.append(el("label", undefined, "SSH port"));
    const sshPortInput = el("input");
    sshPortInput.type = "number";
    sshPortInput.min = "1";
    sshPortInput.max = "65535";
    sshPortInput.placeholder = "22";
    sshPortField.append(sshPortInput);

    const sshKeyField = el("div", "field");
    sshKeyField.append(el("label", undefined, "Private key file"));
    const sshKeyRow = el("div", "input-with-button");
    const sshKeyInput = el("input");
    sshKeyInput.type = "text";
    sshKeyInput.placeholder = "~/.ssh/id_ed25519";
    sshKeyInput.autocomplete = "off";
    sshKeyInput.spellcheck = false;
    const sshKeyBrowse = button("Browse…", "btn ghost small");
    sshKeyBrowse.type = "button";
    sshKeyBrowse.onclick = () => {
      void (async () => {
        try {
          const selected = await openFileDialog({
            multiple: false,
            directory: false,
            title: "Select SSH private key",
          });
          if (typeof selected === "string") sshKeyInput.value = selected;
        } catch (error) {
          formError.textContent = errorText(error);
          formError.classList.remove("hidden");
        }
      })();
    };
    sshKeyRow.append(sshKeyInput, sshKeyBrowse);
    sshKeyField.append(
      sshKeyRow,
      el("span", "field-hint", "The private key itself, not the .pub file."),
    );

    const sshRemoteField = el("div", "field");
    sshRemoteField.append(el("label", undefined, "Desktop bridge port on the remote loopback"));
    const sshRemoteInput = el("input");
    sshRemoteInput.type = "number";
    sshRemoteInput.min = "1";
    sshRemoteInput.max = "65535";
    sshRemoteInput.placeholder = "18791";
    sshRemoteField.append(sshRemoteInput);
    sshBlock.append(sshHostField, sshPortField, sshKeyField, sshRemoteField);

    const ipBlock = el("div", "form-block hidden");
    const ipNotice = el("p", "notice");
    ipNotice.append(
      "Direct IP mode speaks ",
      el("code", undefined, "wss://"),
      " to the desktop bridge. It is opt-in: plaintext ws:// exposes the device pairing handshake, so direct mode requires TLS for non-loopback hosts.",
    );
    const ipField = el("div", "field");
    ipField.append(el("label", undefined, "Endpoint"));
    const ipInput = el("input");
    ipInput.type = "text";
    ipInput.placeholder = "wss://vps.example.com:18791";
    ipField.append(ipInput);
    ipBlock.append(ipNotice, ipField);

    const autoField = el("label", "checkbox-row");
    const autoInput = el("input");
    autoInput.type = "checkbox";
    autoField.append(autoInput, el("span", undefined, "Connect automatically on launch"));

    const formError = el("p", "form-error hidden");

    const buttonRow = el("div", "btn-row");
    const saveBtn = button("Save profile", "btn primary");
    saveBtn.type = "submit";
    const cancelBtn = button("Cancel", "btn ghost");
    const forgetBtn = button("Forget token", "btn danger hidden");
    buttonRow.append(saveBtn, cancelBtn, forgetBtn);

    form.append(
      nameField,
      modeField,
      sshBlock,
      ipBlock,
      autoField,
      formError,
      buttonRow,
    );

    const syncModeBlocks = (): void => {
      const mode = modeSelect.value as ConnectionMode;
      sshBlock.classList.toggle("hidden", mode !== "ssh");
      ipBlock.classList.toggle("hidden", mode !== "ip");
      forgetBtn.classList.toggle("hidden", !(editing && editing.token));
    };

    const openForm = (profile: Profile | null): void => {
      editing = profile;
      formOpen = true;
      const source = profile ?? {
        id: crypto.randomUUID(),
        name: "",
        mode: "local" as ConnectionMode,
      };
      nameInput.value = source.name;
      modeSelect.value = source.mode;
      sshHostInput.value = source.sshHost ?? "";
      sshPortInput.value = source.sshPort ? String(source.sshPort) : "22";
      sshKeyInput.value = source.sshKey ?? "";
      sshRemoteInput.value = source.sshRemotePort ? String(source.sshRemotePort) : "18791";
      ipInput.value = source.url ?? "";
      autoInput.checked = source.autoConnect ?? false;
      formError.classList.add("hidden");
      form.classList.remove("hidden");
      syncModeBlocks();
      nameInput.focus();
      render();
    };

    const closeForm = (): void => {
      formOpen = false;
      editing = null;
      form.classList.add("hidden");
      render();
    };

    modeSelect.addEventListener("change", syncModeBlocks);

    const numberOrNull = (value: string): number | null => {
      const parsed = Number(value);
      return Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed) : null;
    };

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void (async () => {
        const mode = modeSelect.value as ConnectionMode;
        const profile: Profile = {
          id: editing?.id ?? crypto.randomUUID(),
          name: nameInput.value.trim() || mode,
          mode,
          sshHost: mode === "ssh" ? sshHostInput.value.trim() || null : null,
          sshPort: mode === "ssh" ? numberOrNull(sshPortInput.value) : null,
          sshKey: mode === "ssh" ? sshKeyInput.value.trim() || null : null,
          sshRemotePort: mode === "ssh" ? numberOrNull(sshRemoteInput.value) : null,
          url: mode === "ip" ? ipInput.value.trim() || null : null,
          token: editing?.token ?? null,
          autoConnect: autoInput.checked,
        };
        if (mode === "ssh" && !profile.sshHost) {
          formError.textContent = "SSH mode needs a target (user@host).";
          formError.classList.remove("hidden");
          return;
        }
        if (mode === "ssh" && !profile.sshKey) {
          formError.textContent = "SSH mode needs a private key file.";
          formError.classList.remove("hidden");
          return;
        }
        if (mode === "ip") {
          const url = profile.url ?? "";
          if (!url.startsWith("wss://") && !url.startsWith("ws://")) {
            formError.textContent = "IP mode needs a wss:// URL.";
            formError.classList.remove("hidden");
            return;
          }
        }
        try {
          await ctx.actions.saveProfile(profile);
          closeForm();
        } catch (error) {
          formError.textContent = errorText(error);
          formError.classList.remove("hidden");
        }
      })();
    });

    cancelBtn.onclick = () => {
      closeForm();
    };
    forgetBtn.onclick = () => {
      const target = editing;
      if (!target) return;
      void (async () => {
        try {
          await ctx.actions.forgetToken(target.id);
          ctx.actions.toast(
            "Device token forgotten — the next connect will ask for pairing.",
          );
          closeForm();
        } catch (error) {
          formError.textContent = errorText(error);
          formError.classList.remove("hidden");
        }
      })();
    };

    // --- local desktop bridge panel --------------------------------------------

    let gateway: GatewayState | null = null;
    let gatewayOutput = "";
    const gatewayPanel = el("div", "panel");
    const refreshGateway = async (): Promise<void> => {
      if (!isTauri) return;
      try {
        gateway = await ctx.actions.localGatewayStatus();
      } catch (error) {
        gatewayOutput = errorText(error);
      }
      render();
    };
    const runGateway = async (action: string): Promise<void> => {
      try {
        const result = await ctx.actions.gatewayCli(action);
        gatewayOutput = `$ sora-desktop ${action}\n${result.output || "(no output)"}`;
      } catch (error) {
        gatewayOutput = errorText(error);
      }
      await refreshGateway();
    };
    const buildGatewayPanel = (): void => {
      gatewayPanel.replaceChildren();
      if (!isTauri) {
        gatewayPanel.append(
          el(
            "p",
            "empty",
            "Running in a browser — the local bridge panel and profile storage live in the desktop app.",
          ),
        );
        return;
      }
      const state = gateway;
      if (!state) {
        gatewayPanel.append(el("p", "empty", "Reading desktop bridge run-state…"));
        return;
      }
      const lines: [string, string][] = [
        ["Run-state file", state.statePath],
        ["Running", state.running ? "yes" : "no"],
        ["Port", state.port ? String(state.port) : "—"],
        ["PID", state.pid ? String(state.pid) : "—"],
        ["Launch mode", state.launchMode ?? "—"],
        ["Started", state.startedAt ? formatTimestamp(state.startedAt) : "—"],
        ["Logs", state.logPath ?? "—"],
      ];
      const list = el("dl", "kv");
      for (const [label, value] of lines) {
        list.append(el("dt", undefined, label));
        list.append(el("dd", undefined, value));
      }
      gatewayPanel.append(list);

      const actionsRow = el("div", "btn-row");
      for (const action of ["start", "restart", "stop", "logs"]) {
        const btn = button(action === "logs" ? "Show logs" : action[0].toUpperCase() + action.slice(1), "btn ghost");
        btn.onclick = () => {
          void runGateway(action);
        };
        actionsRow.append(btn);
      }
      const refreshBtn = button("Refresh", "btn ghost");
      refreshBtn.onclick = () => {
        void refreshGateway();
      };
      actionsRow.append(refreshBtn);
      gatewayPanel.append(actionsRow);

      if (gatewayOutput) {
        gatewayPanel.append(el("pre", "pre", truncate(gatewayOutput, 4000)));
      }
    };

    // --- dev backend section -----------------------------------------------------

    const devPanel = el("div", "panel hidden");
    if (!isTauri) {
      devPanel.classList.remove("hidden");
      const note = el("p", "notice");
      note.append(
        "Browser dev backend: the UI talks straight to ",
        el("code", undefined, "ws://127.0.0.1:<port>"),
        ". Run ",
        el("code", undefined, "npm run mock"),
        " in another terminal.",
      );
      devPanel.append(note);
      const portField = el("div", "field");
      portField.append(el("label", undefined, "Mock bridge port"));
      const portInput = el("input");
      portInput.type = "number";
      portInput.min = "1";
      portInput.max = "65535";
      const devPortValue = numberOrNull(localStorage.getItem("soradesk.dev.port") ?? "");
      portInput.value = String(devPortValue ?? 18791);
      portField.append(portInput);
      const saveDev = button("Save dev port", "btn ghost");
      saveDev.onclick = () => {
        ctx.actions.setDevPort(numberOrNull(portInput.value) ?? 18791);
      };
      devPanel.append(portField, saveDev);
    }

    // --- updates -----------------------------------------------------------------

    type UpdatePhase = "idle" | "checking" | "current" | "available" | "installing" | "error";

    let updatePhase: UpdatePhase = "idle";
    let updateMessage = "Updates are served from GitHub Releases.";
    let availableUpdate: Update | null = null;

    const checkForUpdates = async (): Promise<void> => {
      if (!isTauri || updatePhase === "checking" || updatePhase === "installing") return;
      updatePhase = "checking";
      updateMessage = "Checking for updates…";
      render();
      try {
        const { check } = await import("@tauri-apps/plugin-updater");
        const update = await check();
        availableUpdate = update;
        if (update) {
          updatePhase = "available";
          updateMessage = `SoraDesk ${update.version} is available (current ${APP_VERSION}).`;
        } else {
          updatePhase = "current";
          updateMessage = `SoraDesk ${APP_VERSION} is up to date.`;
        }
      } catch (error) {
        updatePhase = "error";
        updateMessage = `Update check failed: ${errorText(error)}`;
      }
      render();
    };

    const installUpdate = async (): Promise<void> => {
      if (!availableUpdate || updatePhase === "installing") return;
      updatePhase = "installing";
      updateMessage = "Downloading update…";
      render();
      try {
        await availableUpdate.downloadAndInstall();
        updateMessage = "Update installed — restarting…";
        render();
        const { relaunch } = await import("@tauri-apps/plugin-process");
        await relaunch();
      } catch (error) {
        updatePhase = "error";
        updateMessage = `Update failed: ${errorText(error)}`;
        render();
      }
    };

    // --- render -------------------------------------------------------------------

    const render = (): void => {
      const state = ctx.store.get();
      scroll.replaceChildren();

      const profilesSection = el("section", "section");
      profilesSection.append(el("h3", undefined, "Connection profiles"));
      profilesSection.append(
        el(
          "p",
          "subtle",
          isTauri
            ? "Stored in the app config directory. Tokens are revocable device tokens — never passwords."
            : "Profile editing is available in the desktop app; the browser dev backend uses a single synthetic profile.",
        ),
      );
      const profileList = el("div", "list");
      for (const profile of state.profiles.profiles) {
        const row = el("div", "list-row profile-row");
        const main = el("div", "list-row-main");
        const topLine = el("div", "profile-line");
        topLine.append(el("span", "list-title", profile.name), modeBadge(profile.mode));
        if (profile.id === state.profiles.activeId) {
          topLine.append(el("span", "badge ok", "active"));
        }
        if (profile.token) {
          topLine.append(el("span", "badge muted", maskToken(profile.token)));
        }
        main.append(topLine);
        const detail = describeProfile(profile);
        if (detail) main.append(el("span", "subtle", detail));
        row.append(main);
        const rowActions = el("div", "btn-row");
        if (isTauri) {
          if (profile.id !== state.profiles.activeId) {
            const useBtn = button("Use", "btn ghost");
            useBtn.onclick = () => {
              void ctx.actions.setActiveProfile(profile.id);
            };
            rowActions.append(useBtn);
          }
          const editBtn = button("Edit", "btn ghost");
          editBtn.onclick = () => {
            openForm(profile);
          };
          const deleteBtn = button("Delete", "btn danger");
          deleteBtn.onclick = () => {
            void (async () => {
              await ctx.actions.deleteProfile(profile.id);
              if (editing?.id === profile.id) closeForm();
            })();
          };
          rowActions.append(editBtn, deleteBtn);
        }
        row.append(rowActions);
        profileList.append(row);
      }
      if (state.profiles.profiles.length === 0) {
        profileList.append(el("p", "empty", "No profiles yet."));
      }
      profilesSection.append(profileList);

      if (isTauri) {
        const addBtn = button("Add profile", "btn primary");
        addBtn.onclick = () => {
          openForm(null);
        };
        profilesSection.append(addBtn);
      }
      if (formOpen) profilesSection.append(form);
      scroll.append(profilesSection);

      const gatewaySection = el("section", "section");
      gatewaySection.append(el("h3", undefined, "Local desktop bridge"));
      buildGatewayPanel();
      gatewaySection.append(gatewayPanel);
      scroll.append(gatewaySection);

      if (!isTauri) {
        const devSection = el("section", "section");
        devSection.append(el("h3", undefined, "Browser dev backend"));
        devSection.append(devPanel);
        scroll.append(devSection);
      }

      const aboutSection = el("section", "section");
      aboutSection.append(el("h3", undefined, "About"));
      const aboutList = el("dl", "kv");
      aboutList.append(el("dt", undefined, "SoraDesk"), el("dd", undefined, APP_VERSION));
      aboutList.append(
        el("dt", undefined, "Protocol"),
        el("dd", undefined, `v${PROTOCOL_VERSION} (docs/protocol.md)`),
      );
      aboutList.append(
        el("dt", undefined, "Licence"),
        el("dd", undefined, "AGPL-3.0-only (see LICENSE)"),
      );
      aboutSection.append(aboutList);
      scroll.append(aboutSection);

      if (isTauri) {
        const updatesSection = el("section", "section");
        updatesSection.append(el("h3", undefined, "Updates"));
        const updatesPanel = el("div", "panel");
        updatesPanel.append(el("p", "subtle", updateMessage));
        const updatesRow = el("div", "btn-row");
        const checkBtn = button("Check for updates", "btn ghost");
        checkBtn.disabled = updatePhase === "checking" || updatePhase === "installing";
        checkBtn.onclick = () => void checkForUpdates();
        updatesRow.append(checkBtn);
        if (availableUpdate) {
          const installBtn = button(
            updatePhase === "installing" ? "Installing…" : "Install and restart",
            "btn primary",
          );
          installBtn.disabled = updatePhase === "installing";
          installBtn.onclick = () => void installUpdate();
          updatesRow.append(installBtn);
        }
        updatesPanel.append(updatesRow);
        updatesSection.append(updatesPanel);
        scroll.append(updatesSection);
      }
    };

    const describeProfile = (profile: Profile): string => {
      if (profile.mode === "ssh") {
        const port = profile.sshPort ? ` port ${profile.sshPort}` : "";
        const remote = profile.sshRemotePort ? ` → remote ${profile.sshRemotePort}` : "";
        return `${profile.sshHost ?? "no host"}${port}${remote}`;
      }
      if (profile.mode === "ip") return profile.url ?? "no endpoint";
      return "reads ~/.sora_agent/run/desktop.json";
    };

    const unsubscribe = ctx.store.subscribe(render);
    render();
    void refreshGateway();

    return () => {
      unsubscribe();
    };
  },
};
