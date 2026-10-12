import { Button, ButtonGroup, Divider, HTMLSelect, Icon, Menu, MenuDivider, MenuItem, Switch, Tag, Tooltip } from "@blueprintjs/core";
import cytoscape, { type Core } from "cytoscape";
import fcose from "cytoscape-fcose";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast, useActions } from "../actions";
import { useStore } from "../store";
import { C, EDGE_COLOR, SCHEMA_COLOR, nodeIcon } from "../theme";
import type { Schema } from "../types";

cytoscape.use(fcose);

const EDGE_TYPES = Object.keys(EDGE_COLOR);

function style(showEdgeLabels: boolean): any[] {
  return [
    {
      selector: "node",
      style: {
        width: 36,
        height: 36,
        shape: "ellipse",
        "background-color": C.bg3,
        "background-image": "data(icon)",
        "background-width": "58%",
        "background-height": "58%",
        "border-width": 2,
        "border-color": "data(color)",
        label: "data(label)",
        color: "#D3D8DE",
        "font-family": "Inter, sans-serif",
        "font-size": 10,
        "text-valign": "bottom",
        "text-margin-y": 5,
        "text-max-width": "130px",
        "text-wrap": "ellipsis",
        "text-background-color": C.bg0,
        "text-background-opacity": 0.75,
        "text-background-padding": "2px",
        "text-background-shape": "roundrectangle",
        "transition-property": "opacity",
        "transition-duration": 150,
      },
    },
    { selector: "node[schema = 'Address']", style: { width: 26, height: 26, "font-size": 8, color: C.faint } },
    { selector: "node[?sanctioned]", style: { "border-color": C.red, "border-width": 3, "background-color": "#3A2326" } },
    { selector: "node[?synthetic]", style: { "border-style": "dashed" } },
    { selector: "node[n_records > 1]", style: { "border-style": "double", "border-width": 5 } },
    {
      selector: "node:selected",
      style: { "underlay-color": C.blue, "underlay-opacity": 0.45, "underlay-padding": 7, "underlay-shape": "ellipse", color: C.text },
    },
    {
      selector: "edge",
      style: {
        width: 1.4,
        "curve-style": "bezier",
        "line-color": "data(color)",
        "target-arrow-color": "data(color)",
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.8,
        opacity: 0.8,
        label: showEdgeLabels ? "data(label)" : "",
        "font-size": 7.5,
        "font-family": "JetBrains Mono, monospace",
        color: C.muted,
        "text-rotation": "autorotate",
        "text-background-color": C.bg1,
        "text-background-opacity": 0.85,
        "text-background-padding": "1px",
      },
    },
    { selector: "edge[type = 'REGISTERED_AT']", style: { "line-style": "dashed", width: 1 } },
    { selector: "edge:selected", style: { width: 3, opacity: 1 } },
    { selector: ".dim", style: { opacity: 0.12 } },
    { selector: "node.hl", style: { "underlay-color": C.gold, "underlay-opacity": 0.4, "underlay-padding": 8, "underlay-shape": "ellipse" } },
    { selector: "edge.hl", style: { width: 3.2, opacity: 1, "z-index": 10 } },
  ];
}

const edgeLabel = (type: string, share: number | null) =>
  share != null ? `${type.replace("_", " ")} ${Math.round(share * 100)}%` : type.replace("_", " ");

export default function GraphView() {
  const { state, dispatch } = useStore();
  const actions = useActions();
  const ref = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; id: string | null } | null>(null);
  const [layoutName, setLayoutName] = useState("fcose");
  const [edgeLabels, setEdgeLabels] = useState(true);
  const [hideAddresses, setHideAddresses] = useState(false);
  const syncingSelection = useRef(false);

  // --- init
  useEffect(() => {
    const cy = cytoscape({
      container: ref.current!,
      style: style(true),
      wheelSensitivity: 0.25,
      minZoom: 0.08,
      maxZoom: 3,
      boxSelectionEnabled: true,
      selectionType: "additive",
    });
    cyRef.current = cy;
    const emitSelection = () => {
      if (syncingSelection.current) return;
      dispatch({ type: "select", ids: cy.$("node:selected").map((n) => n.id()) });
    };
    cy.on("select unselect", "node", () => window.setTimeout(emitSelection, 0));
    cy.on("tap", (e) => {
      if (e.target === cy) {
        setMenu(null);
        cy.$(":selected").unselect();
      }
    });
    cy.on("tap", "node", (e) => {
      setMenu(null);
      const oe = e.originalEvent as MouseEvent;
      if (!oe.shiftKey && !oe.ctrlKey && !oe.metaKey) {
        cy.$("node:selected").not(e.target).unselect();
      }
    });
    cy.on("dbltap", "node", (e) => actions.expand([e.target.id()]));
    cy.on("cxttap", (e) => {
      const p = e.renderedPosition ?? { x: 40, y: 40 };
      const id = e.target === cy ? null : e.target.isNode?.() ? e.target.id() : null;
      if (id && !e.target.selected()) {
        cy.$(":selected").unselect();
        e.target.select();
      }
      setMenu({ x: p.x, y: p.y, id });
    });
    return () => cy.destroy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    cyRef.current?.style(style(edgeLabels) as any);
  }, [edgeLabels]);

  // --- sync model -> cytoscape
  useEffect(() => {
    const cy = cyRef.current!;
    const want = state.nodes;
    cy.batch(() => {
      cy.nodes().forEach((n) => {
        if (!want[n.id()]) n.remove();
      });
      for (const n of Object.values(want)) {
        const data = {
          id: n.id,
          label: n.caption,
          schema: n.schema,
          sanctioned: n.sanctioned,
          synthetic: n.synthetic,
          n_records: n.n_records,
          color: SCHEMA_COLOR[n.schema as Schema] ?? C.muted,
          icon: nodeIcon(n.schema as Schema, n.sanctioned ? "#FFB3B5" : "#F6F7F9"),
        };
        const el = cy.getElementById(n.id);
        if (el.empty()) cy.add({ group: "nodes", data, position: { x: 0, y: 0 } });
        else el.data(data);
      }
      cy.edges().forEach((e) => {
        if (!state.edges[e.id()]) e.remove();
      });
      for (const e of Object.values(state.edges)) {
        if (cy.getElementById(e.id).empty() && want[e.source] && want[e.target]) {
          cy.add({
            group: "edges",
            data: { id: e.id, source: e.source, target: e.target, type: e.type, color: EDGE_COLOR[e.type] ?? C.muted, label: edgeLabel(e.type, e.share) },
          });
        }
      }
    });

    // place new nodes in a ring around their anchor, then relax the layout locally
    if (state.pending.length) {
      const fresh = cy.collection();
      const byAnchor: Record<string, string[]> = {};
      for (const p of state.pending) (byAnchor[p.anchor ?? ""] ??= []).push(p.id);
      for (const [anchor, ids] of Object.entries(byAnchor)) {
        const a = anchor ? cy.getElementById(anchor) : null;
        const center = a && a.nonempty() ? a.position() : { x: cy.width() / 2, y: cy.height() / 2 };
        const r = 90 + ids.length * 4;
        ids.forEach((id, i) => {
          const el = cy.getElementById(id);
          if (el.empty() || id === anchor) return;
          const ang = (2 * Math.PI * i) / ids.length;
          el.position({ x: center.x + r * Math.cos(ang), y: center.y + r * Math.sin(ang) });
          fresh.merge(el);
        });
      }
      dispatch({ type: "placed" });
      if (cy.nodes().length <= 400) {
        cy.layout({
          name: "fcose",
          randomize: false,
          animate: true,
          animationDuration: 450,
          nodeRepulsion: 9000,
          idealEdgeLength: 90,
          nodeSeparation: 60,
          fit: false,
        } as any).run();
      }
      window.setTimeout(() => {
        cy.fit(cy.elements(), 70);
        const z = Math.min(cy.zoom(), 1.3);
        cy.zoom(z);
        cy.center();
      }, 480);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.graphRev]);

  // --- selection model -> cytoscape
  useEffect(() => {
    const cy = cyRef.current!;
    const sel = new Set(state.selected);
    syncingSelection.current = true;
    cy.nodes().forEach((n) => {
      if (sel.has(n.id()) && !n.selected()) n.select();
      else if (!sel.has(n.id()) && n.selected()) n.unselect();
    });
    syncingSelection.current = false;
  }, [state.selected]);

  // --- highlight, time filter, address filter
  const activeInRange = useMemo(() => {
    if (!state.range) return null;
    const withEvents = new Set(state.events.map((e) => e.cluster_id));
    const inRange = new Set(
      state.events.filter((e) => {
        const t = Date.parse(e.date);
        return t >= state.range![0] && t <= state.range![1];
      }).map((e) => e.cluster_id),
    );
    return { withEvents, inRange };
  }, [state.events, state.range]);

  useEffect(() => {
    const cy = cyRef.current!;
    cy.batch(() => {
      cy.elements().removeClass("dim hl");
      cy.nodes().style("display", "element");
      if (hideAddresses) cy.nodes("[schema = 'Address']").style("display", "none");
      if (state.highlight) {
        const hn = new Set(state.highlight.nodes);
        const he = new Set(state.highlight.edges);
        cy.nodes().forEach((n) => void n.addClass(hn.has(n.id()) ? "hl" : "dim"));
        cy.edges().forEach((e) => void e.addClass(he.has(e.id()) ? "hl" : "dim"));
      }
      if (activeInRange) {
        cy.nodes().forEach((n) => {
          if (activeInRange.withEvents.has(n.id()) && !activeInRange.inRange.has(n.id())) n.addClass("dim");
        });
      }
    });
  }, [state.highlight, activeInRange, hideAddresses, state.graphRev]);

  const runLayout = (name: string) => {
    const cy = cyRef.current!;
    const opts: any =
      name === "fcose"
        ? { name, randomize: true, animate: true, nodeRepulsion: 9000, idealEdgeLength: 90 }
        : name === "breadthfirst"
          ? { name, directed: true, spacingFactor: 1.2, animate: true }
          : { name, animate: true, avoidOverlap: true, spacingFactor: 1.1 };
    cy.layout({ ...opts, padding: 60 }).run();
  };

  const sel = state.selected;
  const nCount = Object.keys(state.nodes).length;
  const menuTarget = menu?.id ?? null;

  return (
    <div className="graph-wrap">
      <div ref={ref} className="cy" onContextMenu={(e) => e.preventDefault()} />

      <div className="graph-toolbar">
        <ButtonGroup minimal>
          <Tooltip content="Search around selection (double-click a node)" placement="bottom">
            <Button icon="search-around" text="Search around" disabled={!sel.length} onClick={() => actions.expand(sel)} />
          </Tooltip>
          <Tooltip content="Shortest path between two selected objects" placement="bottom">
            <Button icon="route" text="Path" disabled={sel.length !== 2} onClick={() => actions.path(sel[0], sel[1])} />
          </Tooltip>
          <Tooltip content="Unwind beneficial ownership of the focused object" placement="bottom">
            <Button icon="diagram-tree" text="Owners" disabled={!state.focus} onClick={() => state.focus && actions.ownership(state.focus, "up")} />
          </Tooltip>
          <Tooltip content="Find circular ownership anywhere in the graph" placement="bottom">
            <Button icon="refresh" text="Cycles" onClick={() => actions.cycles()} />
          </Tooltip>
          <Divider />
          <HTMLSelect minimal value={layoutName} onChange={(e) => { setLayoutName(e.target.value); runLayout(e.target.value); }}
            options={[{ value: "fcose", label: "Force" }, { value: "breadthfirst", label: "Hierarchy" }, { value: "concentric", label: "Concentric" }, { value: "circle", label: "Circle" }, { value: "grid", label: "Grid" }]} />
          <Tooltip content="Re-run layout" placement="bottom"><Button icon="layout-auto" onClick={() => runLayout(layoutName)} /></Tooltip>
          <Tooltip content="Zoom to fit" placement="bottom"><Button icon="zoom-to-fit" onClick={() => cyRef.current?.animate({ fit: { eles: cyRef.current.elements(), padding: 60 }, duration: 300 } as any)} /></Tooltip>
          <Tooltip content="Remove selected" placement="bottom"><Button icon="trash" disabled={!sel.length} onClick={() => dispatch({ type: "remove", ids: sel })} /></Tooltip>
          <Tooltip content="Clear graph" placement="bottom"><Button icon="eraser" disabled={!nCount} onClick={() => dispatch({ type: "clear" })} /></Tooltip>
        </ButtonGroup>
        <div className="graph-toggles">
          <Switch checked={edgeLabels} label="Link labels" onChange={() => setEdgeLabels(!edgeLabels)} />
          <Switch checked={hideAddresses} label="Hide addresses" onChange={() => setHideAddresses(!hideAddresses)} />
        </div>
      </div>

      {state.highlight && (
        <div className="highlight-chip">
          <Tag large intent="warning" icon="highlight" onRemove={() => dispatch({ type: "highlight", highlight: null })}>
            {state.highlight.label}
          </Tag>
        </div>
      )}

      {!nCount && (
        <div className="empty-graph">
          <Icon icon="graph" size={42} color={C.line} />
          <h3>Empty investigation</h3>
          <p>Search for a person, company or vessel above, or start from a planted scenario:</p>
          <div className="quickstarts">
            <Button small outlined icon="person" text="Viktor Morozan (synthetic)" onClick={() => actions.addEntity("synth_sanctions:synth-morozan")} />
            <Button small outlined icon="ship" text="Velmora Star (synthetic)" onClick={() => actions.addEntity("synth_sanctions:synth-vessel-velmora")} />
            <Button small outlined icon="refresh" text="Ownership cycles" onClick={() => actions.cycles()} />
          </div>
          <p className="hint">Double-click: search around · Right-click: actions · Shift-click: multi-select</p>
        </div>
      )}

      <div className="graph-legend">
        {(["Person", "Company", "Vessel", "Aircraft", "Address"] as Schema[]).map((s) => (
          <span key={s}><i style={{ borderColor: SCHEMA_COLOR[s] }} />{s}</span>
        ))}
        <span><i style={{ borderColor: C.red, background: "#3A2326" }} />Sanctioned</span>
        <span><i style={{ borderColor: C.muted, borderStyle: "dashed" }} />Synthetic</span>
        <span><i style={{ borderColor: C.muted, borderStyle: "double", borderWidth: 4 }} />Merged</span>
        <span className="legend-count">{nCount} objects · {Object.keys(state.edges).length} links</span>
      </div>

      {menu && (
        <div className="ctx-menu" style={{ left: menu.x, top: menu.y }} onMouseLeave={() => setMenu(null)}>
          <Menu>
            {menuTarget ? (
              <>
                <MenuItem icon="search-around" text="Search around" onClick={() => { actions.expand(sel.length ? sel : [menuTarget]); setMenu(null); }} />
                <MenuItem icon="filter" text="Search around by link type">
                  {EDGE_TYPES.map((t) => (
                    <MenuItem key={t} text={t.replace("_", " ")} labelElement={<span className="swatch" style={{ background: EDGE_COLOR[t] }} />}
                      onClick={() => { actions.expand([menuTarget], [t]); setMenu(null); }} />
                  ))}
                </MenuItem>
                <MenuItem icon="layout-hierarchy" text="k-hop neighbourhood">
                  {[1, 2, 3].map((k) => <MenuItem key={k} text={`${k} hop${k > 1 ? "s" : ""}`} onClick={() => { actions.khop(menuTarget, k); setMenu(null); }} />)}
                </MenuItem>
                <MenuDivider title="Analysis" />
                <MenuItem icon="diagram-tree" text="Unwind owners (UBO)" onClick={() => { actions.ownership(menuTarget, "up"); setMenu(null); }} />
                <MenuItem icon="flow-branch" text="Unwind holdings" onClick={() => { actions.ownership(menuTarget, "down"); setMenu(null); }} />
                <MenuItem icon="route" text="Shortest path to other selected" disabled={sel.length !== 2}
                  onClick={() => { actions.path(sel[0], sel[1]); setMenu(null); }} />
                <MenuItem icon="locate" text="Co-movement (same place, same day)" onClick={() => { actions.coMovement(menuTarget); setMenu(null); }} />
                <MenuDivider />
                <MenuItem icon="map" text="Show on map" onClick={() => { dispatch({ type: "focus", id: menuTarget }); dispatch({ type: "view", view: "map" }); setMenu(null); }} />
                <MenuItem icon="globe" text="Show on globe" onClick={() => { dispatch({ type: "focus", id: menuTarget }); dispatch({ type: "view", view: "globe" }); setMenu(null); }} />
                <MenuItem icon="duplicate" text="Copy object id" onClick={() => { navigator.clipboard?.writeText(menuTarget); toast("Copied id"); setMenu(null); }} />
                <MenuItem icon="trash" intent="danger" text="Remove from graph" onClick={() => { dispatch({ type: "remove", ids: sel.length ? sel : [menuTarget] }); setMenu(null); }} />
              </>
            ) : (
              <>
                <MenuItem icon="select" text="Select all" onClick={() => { cyRef.current?.nodes().select(); setMenu(null); }} />
                <MenuItem icon="layout-auto" text="Re-layout" onClick={() => { runLayout(layoutName); setMenu(null); }} />
                <MenuItem icon="eraser" text="Clear graph" onClick={() => { dispatch({ type: "clear" }); setMenu(null); }} />
              </>
            )}
          </Menu>
        </div>
      )}
    </div>
  );
}
