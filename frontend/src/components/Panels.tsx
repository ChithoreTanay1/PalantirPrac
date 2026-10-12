import { Button, Callout, HTMLSelect, HTMLTable, Icon, InputGroup, NonIdealState, ProgressBar, Spinner, Tag, Tooltip } from "@blueprintjs/core";
import { useEffect, useState } from "react";
import { toast, useActions } from "../actions";
import { api } from "../api";
import { type View, useStore } from "../store";
import { sourceLabel } from "../theme";
import { SourceTag } from "./Inspector";

const NAV: { view: View; icon: any; label: string }[] = [
  { view: "graph", icon: "graph", label: "Graph" },
  { view: "map", icon: "map", label: "Map" },
  { view: "globe", icon: "globe-network", label: "Globe" },
  { view: "vessels", icon: "ship", label: "Vessels" },
  { view: "aircraft", icon: "airplane", label: "Aircraft" },
  { view: "markets", icon: "timeline-line-chart", label: "Markets" },
  { view: "resolution", icon: "merge-columns", label: "Resolve" },
  { view: "data", icon: "database", label: "Data" },
  { view: "audit", icon: "history", label: "Audit" },
];

export function NavRail() {
  const { state, dispatch } = useStore();
  return (
    <div className="navrail">
      {NAV.map((n) => (
        <Tooltip key={n.view} content={n.label} placement="right">
          <button className={`nav-btn ${state.view === n.view ? "active" : ""}`} onClick={() => dispatch({ type: "view", view: n.view })}>
            <Icon icon={n.icon} size={18} />
            <span>{n.label}</span>
          </button>
        </Tooltip>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ Resolution review queue

function RecordCol({ r }: { r: any }) {
  if (!r) return null;
  return (
    <div className="er-col">
      <SourceTag source={r.source} />
      <div className="er-name">{r.caption}</div>
      <div className="small muted">{r.schema} · {r.countries.join(", ") || "no country"}{r.birth_date ? ` · born ${r.birth_date}` : ""}</div>
      {r.names.length > 1 && <div className="small muted ellipsis" title={r.names.join(" | ")}>aka {r.names.slice(1, 4).join(" · ")}</div>}
      {r.identifiers.length > 0 && <div className="small mono ellipsis">{r.identifiers.slice(0, 3).join(", ")}</div>}
    </div>
  );
}

export function ResolutionView() {
  const actions = useActions();
  const [queue, setQueue] = useState<any[] | null>(null);
  const [metrics, setMetrics] = useState<any>(null);
  const [schema, setSchema] = useState("");
  const [reason, setReason] = useState("");

  const load = () => {
    setQueue(null);
    api.erQueue(schema || undefined).then(setQueue).catch(() => setQueue([]));
    api.erEval().then(setMetrics).catch(() => {});
  };
  useEffect(load, [schema]);

  const decide = async (p: any, j: "positive" | "negative") => {
    try {
      await api.judge(p.a, p.b, j, reason || (j === "positive" ? "Reviewed: same entity" : "Reviewed: different entities"));
      setQueue((q) => (q ?? []).filter((x) => !(x.a === p.a && x.b === p.b)));
      toast(j === "positive" ? "Merged · clusters recomputed" : "Marked different · will never auto-merge", j === "positive" ? "success" : "none");
      api.erEval().then(setMetrics);
    } catch (e: any) {
      toast(e.message, "danger");
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Entity resolution · review queue</h2>
          <p className="muted">Pairs scoring between the review floor and the auto-merge threshold. Every decision is recorded with your user and reason and can be reversed.</p>
        </div>
        {metrics && (
          <div className="metric-row">
            <Metric label="Precision" value={metrics.precision} />
            <Metric label="Recall" value={metrics.recall} />
            <Metric label="F1" value={metrics.f1} />
            <div className="metric small muted">{metrics.records_scored.toLocaleString()} records with ground truth<br />{metrics.predicted_pairs.toLocaleString()} predicted / {metrics.true_pairs.toLocaleString()} true pairs</div>
          </div>
        )}
      </div>
      <div className="er-toolbar">
        <HTMLSelect value={schema} onChange={(e) => setSchema(e.target.value)} options={[{ value: "", label: "All types" }, "Person", "Company", "Vessel", "Aircraft"]} />
        <InputGroup leftIcon="annotation" placeholder="Reason recorded with your decisions (optional)" value={reason} onChange={(e) => setReason(e.target.value)} className="grow" />
        <Button icon="refresh" text="Reload" onClick={load} />
      </div>
      {!queue && <Spinner className="mt20" />}
      {queue && !queue.length && <NonIdealState icon="tick-circle" title="Queue empty" description="No unresolved grey-zone pairs at your clearance." />}
      <div className="er-list">
        {queue?.map((p) => (
          <div key={p.a + p.b} className="er-card">
            <RecordCol r={p.record_a} />
            <div className="er-mid">
              <div className="er-score">{Math.round(p.score * 100)}%</div>
              <ProgressBar value={p.score} stripes={false} intent="warning" />
              <div className="features small">
                <span>name {p.features.name}</span>
                {p.features.birth_date === 1 && <span className="good">DOB match</span>}
                {p.features.birth_date === -1 && <span className="bad">DOB conflict</span>}
                {p.features.country === 1 && <span className="good">country</span>}
                {p.features.country === -1 && <span className="bad">country ≠</span>}
                {p.features.shared_ids?.length > 0 && <span className="good">shared id</span>}
              </div>
              <div className="row-actions">
                <Button small intent="success" icon="tick" text="Same" onClick={() => decide(p, "positive")} />
                <Button small intent="danger" outlined icon="cross" text="Different" onClick={() => decide(p, "negative")} />
              </div>
              <Button small minimal icon="graph" text="Open both" onClick={() => { actions.addEntity(p.a); actions.addEntity(p.b); }} />
            </div>
            <RecordCol r={p.record_b} />
          </div>
        ))}
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="metric">
      <div className="metric-v">{(value * 100).toFixed(1)}%</div>
      <div className="metric-l">{label}</div>
    </div>
  );
}

// ------------------------------------------------------------------ Data / lineage

export function DataView() {
  const [stats, setStats] = useState<any>(null);
  const [analytics, setAnalytics] = useState<any[]>([]);
  useEffect(() => {
    api.stats().then(setStats).catch(() => {});
    api.analytics().then(setAnalytics).catch(() => {});
  }, []);
  if (!stats) return <div className="page"><Spinner /></div>;
  const bySource: Record<string, Record<string, number>> = {};
  for (const r of stats.records) (bySource[r.source] ??= {})[r.schema] = r.n;
  const schemas = ["Person", "Company", "Vessel", "Aircraft", "Address"];
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Data sources & lineage</h2>
          <p className="muted">Every object is ingested with source, document, ingest run and content hash. Re-runs are idempotent.</p>
        </div>
        <div className="metric-row">
          <div className="metric"><div className="metric-v">{stats.clusters.toLocaleString()}</div><div className="metric-l">Resolved objects</div></div>
          <div className="metric"><div className="metric-v">{stats.edges.toLocaleString()}</div><div className="metric-l">Links</div></div>
          <div className="metric"><div className="metric-v">{stats.events.toLocaleString()}</div><div className="metric-l">Events</div></div>
        </div>
      </div>
      <Callout intent="warning" icon="warning-sign" className="mb10">
        Real data: OpenSanctions (OFAC SDN, EU FSF). Synthetic data (flagged everywhere): fictional ICIJ-format, Companies House-format and sanctions-format records with planted patterns.
        Stay on entities that appear in sanctions lists, indictments or leaks. A match is a lead, not proof.
      </Callout>
      <h4>Records by source</h4>
      <HTMLTable compact striped className="full">
        <thead><tr><th>Source</th>{schemas.map((s) => <th key={s}>{s}</th>)}<th>Total</th></tr></thead>
        <tbody>
          {Object.entries(bySource).map(([src, m]) => (
            <tr key={src}><td><SourceTag source={src} /></td>{schemas.map((s) => <td key={s}>{(m[s] ?? 0).toLocaleString()}</td>)}
              <td><b>{Object.values(m).reduce((a, b) => a + b, 0).toLocaleString()}</b></td></tr>
          ))}
        </tbody>
      </HTMLTable>
      <h4>Ingest runs</h4>
      <HTMLTable compact striped className="full">
        <thead><tr><th>Run</th><th>Source</th><th>Started</th><th>Inserted</th><th>Updated</th><th>Unchanged</th><th>File hash</th></tr></thead>
        <tbody>
          {stats.runs.map((r: any) => (
            <tr key={r.id}><td className="mono">{r.id}</td><td>{sourceLabel(r.source)}</td><td>{r.started_at}</td><td>{r.inserted}</td><td>{r.updated}</td><td>{r.unchanged}</td><td className="mono small">{(r.file_hash ?? "").slice(0, 12)}</td></tr>
          ))}
        </tbody>
      </HTMLTable>
      <h4>Parquet staging layer (DuckDB)</h4>
      <HTMLTable compact striped className="full">
        <thead><tr><th>Source</th><th>Schema</th><th>Rows</th><th>Distinct captions</th><th>With identifiers</th></tr></thead>
        <tbody>
          {analytics.map((r, i) => (
            <tr key={i}><td>{sourceLabel(r.source)}</td><td>{r.schema}</td><td>{r.n.toLocaleString()}</td><td>{r.distinct_captions.toLocaleString()}</td><td>{r.with_identifiers.toLocaleString()}</td></tr>
          ))}
        </tbody>
      </HTMLTable>
    </div>
  );
}

// ------------------------------------------------------------------ Audit

export function AuditView() {
  const { state } = useStore();
  const [log, setLog] = useState<any[] | null>(null);
  useEffect(() => {
    api.audit().then(setLog).catch(() => setLog([]));
  }, [state.user?.username]);
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Audit log</h2>
          <p className="muted">Every search, view, traversal and resolution decision is logged. {state.user && state.user.clearance < 2 ? "You see your own activity; admins see everyone's." : "Showing all users."}</p>
        </div>
      </div>
      {!log ? <Spinner /> : (
        <HTMLTable compact striped className="full">
          <thead><tr><th>Time (UTC)</th><th>User</th><th>Action</th><th>Object</th><th>Detail</th></tr></thead>
          <tbody>
            {log.map((r) => (
              <tr key={r.id}>
                <td className="mono small">{r.ts.replace("T", " ").slice(0, 19)}</td>
                <td>{r.username}</td>
                <td><Tag minimal>{r.action}</Tag></td>
                <td className="mono small ellipsis maxw">{r.object_id}</td>
                <td className="mono small ellipsis maxw">{r.detail}</td>
              </tr>
            ))}
          </tbody>
        </HTMLTable>
      )}
    </div>
  );
}
