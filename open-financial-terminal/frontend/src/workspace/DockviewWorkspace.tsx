import { DockviewReact, type DockviewReadyEvent } from "dockview";
import { dockviewComponents } from "./widgetRegistry";
import { RightHeaderActions } from "./HeaderActions";
import { redockFloating, retitlePanels, seedDefaultLayout } from "./layoutUtil";
import { useWorkspace } from "../state/workspace";
import { useSettings } from "../state/settings";
import { api as http } from "../api/client";

export default function DockviewWorkspace() {
  const setApi = useWorkspace((s) => s.setApi);

  const onReady = async (event: DockviewReadyEvent) => {
    setApi(event.api);
    const wsStore = useWorkspace.getState();

    // Reopen the last-active bento space; fall back to "default", seeding it on first run.
    const last = useSettings.getState().lastWorkspace || "default";
    let restored = false;
    for (const name of [last, "default"]) {
      try {
        const ws = await http.loadWorkspace(name);
        event.api.fromJSON(ws.layout as never);
        redockFloating(event.api);
        retitlePanels(event.api, useSettings.getState().language);
        wsStore.setCurrent(name);
        useSettings.getState().setLastWorkspace(name);
        restored = true;
        break;
      } catch {
        /* not found — try the next */
      }
    }
    if (!restored) {
      seedDefaultLayout(event.api);
      wsStore.setCurrent("default");
      await http.saveWorkspace("default", event.api.toJSON()).catch(() => {});
    }
    await wsStore.refreshList();
    await wsStore.refreshTemplates();

    event.api.onDidLayoutChange(() => useWorkspace.getState().scheduleSave());
  };

  return (
    <DockviewReact
      components={dockviewComponents}
      rightHeaderActionsComponent={RightHeaderActions}
      onReady={onReady}
      className="oft-dockview h-full w-full"
    />
  );
}
