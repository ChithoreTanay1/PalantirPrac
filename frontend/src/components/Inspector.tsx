import { AnchorButton, Button, Callout, HTMLTable, Icon, NonIdealState, ProgressBar, Spinner, Tab, Tabs, Tag, Tooltip } from "@blueprintjs/core";
import { useEffect, useState } from "react";
import { toast, useActions } from "../actions";
import { api } from "../api";
import { useStore } from "../store";
import { CLASSIFICATION, SCHEMA_COLOR, SCHEMA_ICON, eventColor, sourceLabel } from "../theme";
import type { EntityDetail, Schema } from "../types";

export function ObjectIcon({ schema, sanctioned, size = 18 }: { schema: Schema; sanctioned?: boolean; size?: number }) {
  return (
    <span className="obj-icon" style={{ borderColor: sanctioned ? "#E76A6E" : SCHEMA_COLOR[schema], width: size + 12, height: size + 12 }}>
      <Icon icon={SCHEMA_ICON[schema] as any} size={size * 0.75} color={sanctioned ? "#FFB3B5" : "#F6F7F9"} />
    </span>
  );
}

export function SourceTag({ source }: { source: string }) {
  const synthetic = source.startsWith("synth");
  return <Tag minimal className={`src-tag ${synthetic ? "synthetic" : ""}`}>{sourceLabel(source)}</Tag>;
}

const PROP_LABEL: Record<string, string> = {
  name: "Names", country: "Countries", identifier: "Identifiers", birthDate: "Date of birth", alias: "Aliases",
  position: "Position", topics: "Topics", incorporation_date: "Incorporated", jurisdiction_description: "Jurisdiction",
  status: "Status", full: "Address", imoNumber: "IMO", type: "Type", flag: "Flag", programId: "Programs",
};

export default function Inspector() {
  const { state, dispatch } = useStore();
  const actions = useActions();
  const [ent, setEnt] = useState<EntityDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState("overview");
  const [rev, setRev] = useState(0);
  const id = state.focus;

  useEffect(() => {
    if (!id) return setEnt(null);
    let alive = true;
    setLoading(true);
    api.entity(id)
      .then((e) => alive && setEnt(e))
      .catch(() => alive && setEnt(null))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [id, rev, state.user?.username]);

  if (!id) {
    return (
      <div className="inspector">
        <NonIdealState icon="select" title="No object selected" description="Select an object on the graph, map or in search results to inspect it." />
      </div>
    );
  }
  if (!ent) {
    return <div className="inspector">{loading ? <Spinner size={24} className="mt20" /> : <NonIdealState icon="lock" title="Not available" description="Object not found or above your clearance." />}</div>;
  }

  const s = ent.summary;
  const events = state.events.filter((e) => e.cluster_id === s.id);
  const cls = CLASSIFICATION[s.classification];

  const afterResolution = async (p: Promise<any>, msg: string) => {
    try {
      const res = await p;
      toast(msg, "success");
      const newId = res.cluster ?? s.id;
      await actions.refreshNodes([s.id, newId, ...ent.suggestions.map((x) => x.cluster.id)].filter((x) => state.nodes[x] || x === newId));
      dispatch({ type: "focus", id: newId });
      setRev((r) => r + 1);
    } catch (e: any) {
      toast(`Failed: ${e.message}`, "danger");
    }
  };

  return (
    <div className="inspector">
      <div className="insp-head">
        <ObjectIcon schema={s.schema} sanctioned={s.sanctioned} size={22} />
        <div className="insp-title">
          <div className="insp-caption" title={s.caption}>{s.caption}</div>
          <div className="insp-tags">
            <Tag minimal style={{ color: SCHEMA_COLOR[s.schema] }}>{s.schema.toUpperCase()}</Tag>
            {s.sanctioned && <Tag intent="danger" icon="ban-circle">SANCTIONED</Tag>}
            {s.synthetic && <Tag minimal intent="warning">SYNTHETIC</Tag>}
            {s.n_records > 1 && <Tag minimal intent="primary" icon="merge-columns">{s.n_records} records</Tag>}
            <Tag minimal style={{ color: cls.color }}>{cls.label}</Tag>
          </div>
        </div>
      </div>
      <div className="insp-actions">
        <Tooltip content="Search around"><Button small minimal icon="search-around" onClick={() => actions.expand([s.id])} /></Tooltip>
        <Tooltip content="Unwind owners"><Button small minimal icon="diagram-tree" onClick={() => actions.ownership(s.id, "up")} /></Tooltip>
        <Tooltip content="Unwind holdings"><Button small minimal icon="flow-branch" onClick={() => actions.ownership(s.id, "down")} /></Tooltip>
        <Tooltip content="2-hop neighbourhood"><Button small minimal icon="layout-hierarchy" onClick={() => actions.khop(s.id, 2)} /></Tooltip>
        <Tooltip content="Co-movement"><Button small minimal icon="locate" onClick={() => actions.coMovement(s.id)} /></Tooltip>
        <Tooltip content="Show on map"><Button small minimal icon="map" onClick={() => dispatch({ type: "view", view: "map" })} /></Tooltip>
        {!state.nodes[s.id] && <Button small intent="primary" icon="add" text="Add to graph" onClick={() => actions.addEntity(s.id)} />}
      </div>

      <Tabs id="insp" selectedTabId={tab} onChange={(t) => setTab(String(t))} className="insp-tabs" renderActiveTabPanelOnly>
        <Tab id="overview" title="Overview" panel={<Overview ent={ent} events={events.length} />} />
        <Tab id="props" title="Properties" panel={<Properties ent={ent} />} />
        <Tab id="sources" title={`Sources ${ent.records.length}`} panel={<Sources ent={ent} />} />
        <Tab id="er" title="Resolution" panel={
          <Resolution ent={ent}
            onSplit={(rid) => afterResolution(api.unmerge(rid, "Analyst split from inspector"), "Record split out; clusters recomputed")}
            onMerge={(a, b) => afterResolution(api.judge(a, b, "positive", "Analyst merge from inspector"), "Merged; clusters recomputed")}
            onReject={(a, b) => afterResolution(api.judge(a, b, "negative", "Analyst rejected suggestion"), "Marked as different entities")} />
        } />
        <Tab id="events" title={`Events ${events.length}`} panel={
          <div className="insp-scroll">
            {!events.length && <p className="muted small">No events visible at your clearance.</p>}
            {events.map((e) => (
              <div key={e.id} className="event-row">
                <i style={{ background: eventColor(e.type) }} />
                <div>
                  <div><b>{e.type}</b> <span className="muted">{e.date}</span> {e.classification > 0 && <Tag minimal style={{ color: CLASSIFICATION[e.classification].color }}>{CLASSIFICATION[e.classification].label}</Tag>}</div>
                  <div className="small">{e.description}</div>
                  <div className="muted small">{e.place ?? e.country} {e.geo_precision === "country" && "(country centroid)"} · {sourceLabel(e.source)}</div>
                </div>
              </div>
            ))}
          </div>
        } />
      </Tabs>
    </div>
  );
}

function Overview({ ent, events }: { ent: EntityDetail; events: number }) {
  const p = ent.properties;
  const show = ["name", "birthDate", "country", "identifier", "position", "jurisdiction_description", "incorporation_date", "status", "type", "full"];
  return (
    <div className="insp-scroll">
      {ent.sanctions.length > 0 && (
        <Callout intent="danger" icon="ban-circle" title={`Sanctioned · ${ent.sanctions.length} designation${ent.sanctions.length > 1 ? "s" : ""}`} className="mb10">
          {ent.sanctions.slice(0, 6).map((s, i) => (
            <div key={i} className="small sanction-row">
              <b>{s.program}</b> — {s.authority}{s.start_date ? ` · ${s.start_date}` : ""}
              {s.source_url && <a href={s.source_url} target="_blank" rel="noreferrer"> <Icon icon="share" size={10} /></a>}
            </div>
          ))}
        </Callout>
      )}
      <div className="kv-grid">
        <span>Links</span><b>{ent.degree}</b>
        <span>Events</span><b>{events}</b>
        <span>Sources</span><b>{ent.summary.sources.map(sourceLabel).join(", ")}</b>
      </div>
      {show.filter((k) => p[k]?.length).map((k) => (
        <div key={k} className="prop-block">
          <div className="prop-label">{PROP_LABEL[k] ?? k}</div>
          {p[k].slice(0, 8).map((v) => (
            <div key={v.value} className="prop-value">
              <span>{v.value}</span>
              <span className="prov">{v.records.length > 1 ? `${v.records.length} sources` : sourceLabel(v.records[0].split(":")[0])}</span>
            </div>
          ))}
          {p[k].length > 8 && <div className="muted small">+{p[k].length - 8} more</div>}
        </div>
      ))}
      <Callout icon="info-sign" className="mt10 small">
        A match is a lead, not proof. Every value above links back to the source record it came from.
      </Callout>
    </div>
  );
}

function Properties({ ent }: { ent: EntityDetail }) {
  return (
    <div className="insp-scroll">
      <HTMLTable compact className="prop-table">
        <tbody>
          {Object.entries(ent.properties).map(([k, vals]) =>
            vals.slice(0, 20).map((v, i) => (
              <tr key={k + i}>
                <td className="muted">{i === 0 ? PROP_LABEL[k] ?? k : ""}</td>
                <td>{String(v.value)}</td>
                <td>{v.records.map((r) => <SourceTag key={r} source={r.split(":")[0]} />)}</td>
              </tr>
            )),
          )}
        </tbody>
      </HTMLTable>
    </div>
  );
}

function Sources({ ent }: { ent: EntityDetail }) {
  return (
    <div className="insp-scroll">
      {ent.records.map((r) => (
        <div key={r.id} className="record-card">
          <div className="record-head">
            <SourceTag source={r.source} />
            <span className="mono small">{r.source_id}</span>
            {r.source_url && <AnchorButton minimal small icon="share" href={r.source_url} target="_blank" />}
          </div>
          <div className="record-caption">{r.caption}</div>
          <div className="kv-grid small">
            <span>Document</span><span>{r.source_doc}</span>
            <span>Ingest run</span><span className="mono">{r.run_id}</span>
            <span>Ingested</span><span>{r.ingested_at}</span>
            <span>Classification</span><span style={{ color: CLASSIFICATION[r.classification].color }}>{CLASSIFICATION[r.classification].label}</span>
            {r.identifiers.length > 0 && (<><span>Identifiers</span><span className="mono">{r.identifiers.join(", ")}</span></>)}
          </div>
        </div>
      ))}
    </div>
  );
}

function Features({ f }: { f: any }) {
  return (
    <div className="features small">
      <span>name {f.name?.toFixed?.(2)}</span>
      {f.shared_ids?.length > 0 && <span className="good">shared id {f.shared_ids.join(", ")}</span>}
      {f.conflicting_ids?.length > 0 && <span className="bad">conflicting {f.conflicting_ids.join(", ")}</span>}
      {f.birth_date === 1 && <span className="good">DOB match</span>}
      {f.birth_date === -1 && <span className="bad">DOB conflict</span>}
      {f.country === 1 && <span className="good">country overlap</span>}
      {f.country === -1 && <span className="bad">country mismatch</span>}
      {f.names_compared?.[0] && <span className="mono">"{f.names_compared[0]}" ≈ "{f.names_compared[1]}"</span>}
    </div>
  );
}

function Resolution({ ent, onSplit, onMerge, onReject }: {
  ent: EntityDetail; onSplit: (r: string) => void; onMerge: (a: string, b: string) => void; onReject: (a: string, b: string) => void;
}) {
  return (
    <div className="insp-scroll">
      <div className="section-title">Member records</div>
      {ent.records.map((r) => (
        <div key={r.id} className="member-row">
          <SourceTag source={r.source} />
          <span className="grow ellipsis" title={r.caption}>{r.caption}</span>
          {ent.records.length > 1 && <Button small minimal icon="split-columns" text="Split" onClick={() => onSplit(r.id)} />}
        </div>
      ))}
      {ent.evidence.length > 0 && <div className="section-title">Why merged</div>}
      {ent.evidence.map((e) => (
        <div key={e.a + e.b} className="evidence">
          <div className="score-line"><span className="mono small">{e.a.split(":")[0]} ↔ {e.b.split(":")[0]}</span><b>{(e.score * 100).toFixed(0)}%</b></div>
          <ProgressBar value={e.score} stripes={false} intent={e.score >= 0.88 ? "success" : "warning"} />
          <Features f={e.features} />
        </div>
      ))}
      <div className="section-title">Possible matches (below auto-merge)</div>
      {!ent.suggestions.length && <p className="muted small">None.</p>}
      {ent.suggestions.map((sg) => (
        <div key={sg.a + sg.b} className="evidence">
          <div className="score-line">
            <span className="ellipsis">{sg.cluster.caption}</span>
            <b>{(sg.score * 100).toFixed(0)}%</b>
          </div>
          <div className="small muted">{sg.cluster.sources.map(sourceLabel).join(", ")} · {sg.cluster.countries.join(", ")}</div>
          <Features f={sg.features} />
          <div className="row-actions">
            <Button small intent="success" icon="merge-columns" text="Same entity" onClick={() => onMerge(sg.a, sg.b)} />
            <Button small minimal intent="danger" icon="cross" text="Different" onClick={() => onReject(sg.a, sg.b)} />
          </div>
        </div>
      ))}
      {ent.judgements.length > 0 && <div className="section-title">Audit trail</div>}
      {ent.judgements.map((j) => (
        <div key={j.id} className="small judgement">
          <Tag minimal intent={j.judgement === "positive" ? "success" : "danger"}>{j.judgement === "positive" ? "MERGE" : "SPLIT"}</Tag>
          <span className="muted"> {j.created_at.replace("T", " ").slice(0, 19)} · {j.username}</span>
          <div className="muted">{j.reason}</div>
        </div>
      ))}
    </div>
  );
}
