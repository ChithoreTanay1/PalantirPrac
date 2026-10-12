import { OverlayToaster, Position, type Toaster } from "@blueprintjs/core";
import { api } from "./api";
import { useStore } from "./store";

let toasterPromise: Promise<Toaster> | null = null;
export function toast(message: string, intent: "primary" | "success" | "warning" | "danger" | "none" = "none") {
  toasterPromise ??= OverlayToaster.createAsync({ position: Position.BOTTOM_LEFT, maxToasts: 4 });
  toasterPromise.then((t) => t.show({ message, intent, timeout: 3500 }));
}

/** Analyst actions shared by the graph context menu, toolbar, search and inspector. */
export function useActions() {
  const { state, dispatch, run } = useStore();

  const guard = async (label: string, fn: () => Promise<void>) => {
    try {
      await run(fn());
    } catch (e: any) {
      toast(`${label} failed: ${e.message ?? e}`, "danger");
    }
  };

  return {
    addEntity: (id: string) =>
      guard("Add", async () => {
        const g = await api.nodes([id]);
        dispatch({ type: "add", payload: g, select: g.nodes.map((n) => n.id) });
        if (state.view !== "graph" && state.view !== "map" && state.view !== "globe") dispatch({ type: "view", view: "graph" });
      }),

    expand: (ids: string[], types?: string[]) =>
      guard("Search around", async () => {
        const g = await api.expand(ids, types, 80);
        dispatch({ type: "add", payload: g, anchor: ids[0] });
        const added = g.nodes.filter((n) => !state.nodes[n.id]).length;
        toast(`Search around: +${added} objects${g.truncated ? ` (${g.truncated} more not shown)` : ""}`, "primary");
      }),

    khop: (id: string, k: number) =>
      guard("k-hop", async () => {
        const g = await api.khop(id, k);
        dispatch({ type: "add", payload: g, anchor: id });
        toast(`${k}-hop neighbourhood: ${g.nodes.length} objects${g.truncated ? " (truncated)" : ""}`, "primary");
      }),

    path: (a: string, b: string) =>
      guard("Shortest path", async () => {
        const g = await api.path(a, b);
        if (!g.found) return toast("No path found within 8 hops (at your clearance)", "warning");
        dispatch({ type: "add", payload: g, anchor: a });
        dispatch({ type: "highlight", highlight: { nodes: g.path, edges: g.edges.map((e) => e.id), label: `Shortest path · ${g.hops} hops` } });
      }),

    ownership: (id: string, direction: "up" | "down") =>
      guard("Ownership", async () => {
        const g = await api.ownership(id, direction);
        if (g.nodes.length <= 1) return toast(direction === "up" ? "No recorded owners for this object" : "No recorded holdings for this object");
        dispatch({ type: "add", payload: g, anchor: id });
        const cyc = g.cycles.length ? ` · ⚠ ${g.cycles.length} circular` : "";
        dispatch({
          type: "highlight",
          highlight: {
            nodes: g.nodes.map((n) => n.id),
            edges: g.edges.map((e) => e.id),
            label: `${direction === "up" ? "Beneficial ownership" : "Holdings"} · ${g.nodes.length - 1} entities${cyc}`,
          },
        });
        if (g.cycles.length) toast("Circular ownership detected in this chain", "warning");
      }),

    coMovement: (id: string) =>
      guard("Co-movement", async () => {
        const hits = await api.coMovement(id);
        if (!hits.length) return toast("No co-located entities found", "none");
        const ids = [id, ...hits.slice(0, 5).map((h) => h.cluster.id)];
        const g = await api.nodes(ids);
        dispatch({ type: "add", payload: g, anchor: id });
        dispatch({ type: "highlight", highlight: { nodes: ids, edges: [], label: `Co-movement · ${hits[0].cluster.caption} ×${hits[0].count}` } });
        toast(`${hits[0].cluster.caption}: ${hits[0].count} co-located sightings`, "warning");
      }),

    cycles: () =>
      guard("Cycle detection", async () => {
        const cyc = await api.cycles();
        if (!cyc.length) return toast("No circular ownership found", "none");
        const ids = [...new Set(cyc.flatMap((c) => c.nodes.map((n) => n.id)))];
        const g = await api.nodes(ids);
        dispatch({ type: "add", payload: g });
        dispatch({ type: "highlight", highlight: { nodes: ids, edges: g.edges.map((e) => e.id), label: `${cyc.length} ownership cycles` } });
      }),

    refreshNodes: (ids: string[]) =>
      guard("Refresh", async () => {
        const g = await api.nodes(ids);
        dispatch({ type: "remove", ids: ids.filter((i) => !g.nodes.some((n) => n.id === i)) });
        dispatch({ type: "add", payload: g });
      }),
  };
}
