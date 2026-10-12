import { useEffect } from "react";
import { api } from "./api";
import GeoView from "./components/GeoView";
import GraphView from "./components/GraphView";
import Inspector from "./components/Inspector";
import AssetsView from "./components/AssetsView";
import MarketsView from "./components/MarketsView";
import { AuditView, DataView, NavRail, ResolutionView } from "./components/Panels";
import Timeline from "./components/Timeline";
import TopBar, { ClassificationBanner } from "./components/TopBar";
import { useStore } from "./store";

export default function App() {
  const { state, dispatch } = useStore();

  useEffect(() => {
    api.me().then((user) => dispatch({ type: "user", user })).catch(() => {});
  }, [dispatch]);

  const workspace = state.view === "graph" || state.view === "map" || state.view === "globe";

  return (
    <div className="app">
      <ClassificationBanner />
      <TopBar />
      <div className="body">
        <NavRail />
        <div className="main">
          {/* Graph and geo views stay mounted so layout, camera and selection survive view switches */}
          <div className="workspace" style={{ display: workspace ? "flex" : "none" }}>
            <div className="center">
              <div className="stage">
                <div className="stage-layer" style={{ visibility: state.view === "graph" ? "visible" : "hidden" }}>
                  <GraphView />
                </div>
                <div className="stage-layer" style={{ display: state.view !== "graph" ? "block" : "none" }}>
                  <GeoView globe={state.view === "globe"} visible={state.view === "map" || state.view === "globe"} />
                </div>
              </div>
              <Timeline />
            </div>
            <Inspector />
          </div>
          {state.view === "vessels" && <AssetsView key="vessel" kind="vessel" />}
          {state.view === "aircraft" && <AssetsView key="aircraft" kind="aircraft" />}
          {state.view === "markets" && <MarketsView />}
          {state.view === "resolution" && <ResolutionView />}
          {state.view === "data" && <DataView />}
          {state.view === "audit" && <AuditView />}
        </div>
      </div>
      <ClassificationBanner />
    </div>
  );
}
