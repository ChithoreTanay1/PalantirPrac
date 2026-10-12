import { Button, HTMLSelect, Icon, InputGroup, Menu, MenuDivider, MenuItem, Popover, Spinner, Tag } from "@blueprintjs/core";
import { useEffect, useRef, useState } from "react";
import { useActions } from "../actions";
import { api, setApiUser } from "../api";
import { useStore } from "../store";
import { CLASSIFICATION, sourceLabel } from "../theme";
import type { NodeSummary, User } from "../types";
import { ObjectIcon } from "./Inspector";

export function ClassificationBanner() {
  const { state } = useStore();
  const c = CLASSIFICATION[state.user?.clearance ?? 0];
  return (
    <div className="cls-banner" style={{ background: c.color }}>
      {c.label}
      <span className="cls-sub"> // PUBLIC HISTORICAL & SYNTHETIC DATA ONLY // PERSONAL RESEARCH PROJECT</span>
    </div>
  );
}

export default function TopBar() {
  const { state, dispatch } = useStore();
  const actions = useActions();
  const [q, setQ] = useState("");
  const [schema, setSchema] = useState("");
  const [hits, setHits] = useState<NodeSummary[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [users, setUsers] = useState<User[]>([]);
  const timer = useRef<number>();
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.users().then(setUsers).catch(() => {});
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    window.clearTimeout(timer.current);
    if (q.trim().length < 2) return setHits([]);
    timer.current = window.setTimeout(() => {
      setLoading(true);
      api.search(q, schema || undefined).then(setHits).catch(() => setHits([])).finally(() => setLoading(false));
    }, 220);
  }, [q, schema, state.user?.username]);

  const switchUser = async (u: string) => {
    setApiUser(u);
    const me = await api.me();
    dispatch({ type: "user", user: me });
    const ids = Object.keys(state.nodes);
    if (ids.length) {
      // re-fetch the investigation at the new clearance (objects may disappear)
      dispatch({ type: "clear" });
      const g = await api.nodes(ids);
      dispatch({ type: "add", payload: g });
    }
  };

  return (
    <div className="topbar">
      <div className="brand">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          <circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="3.2" /><path d="M12 3v5.8M12 15.2V21M3 12h5.8M15.2 12H21" />
        </svg>
        <span>MINI-GOTHAM</span>
      </div>
      <div className="omnibox">
        <Popover
          isOpen={open && q.trim().length >= 2}
          onInteraction={(next) => setOpen(next)}
          autoFocus={false}
          enforceFocus={false}
          placement="bottom-start"
          minimal
          matchTargetWidth
          content={
            <Menu className="search-results">
              {loading && <MenuItem disabled text={<Spinner size={14} />} />}
              {!loading && !hits.length && <MenuItem disabled text="No objects found (at your clearance)" />}
              {hits.length > 0 && <MenuDivider title={`${hits.length} objects`} />}
              {hits.map((h) => (
                <MenuItem
                  key={h.id}
                  icon={<ObjectIcon schema={h.schema} sanctioned={h.sanctioned} size={12} />}
                  text={
                    <div className="hit">
                      <div className="hit-title">{h.caption} {h.sanctioned && <Tag minimal intent="danger">SANCTIONED</Tag>}{h.synthetic && <Tag minimal intent="warning">SYNTH</Tag>}</div>
                      <div className="hit-sub">{h.schema} · {h.countries.join(", ") || "—"} · {h.sources.map(sourceLabel).join(", ")}{h.n_records > 1 ? ` · ${h.n_records} records` : ""}</div>
                    </div>
                  }
                  labelElement={state.nodes[h.id] ? <Icon icon="tick" /> : <Icon icon="add" />}
                  onClick={() => {
                    actions.addEntity(h.id);
                    setOpen(false);
                  }}
                />
              ))}
            </Menu>
          }
        >
          <InputGroup
            inputRef={inputRef}
            leftIcon="search"
            placeholder="Search people, companies, vessels, aircraft…  (Ctrl+K)"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            rightElement={
              <HTMLSelect minimal value={schema} onChange={(e) => setSchema(e.target.value)}
                options={[{ value: "", label: "All types" }, "Person", "Company", "Vessel", "Aircraft"]} />
            }
          />
        </Popover>
      </div>
      <div className="topbar-right">
        {state.busy > 0 && <Spinner size={16} />}
        <Popover
          placement="bottom-end"
          content={
            <Menu>
              <MenuDivider title="Act as (clearance)" />
              {users.map((u) => (
                <MenuItem key={u.username} text={u.display ?? u.username} active={state.user?.username === u.username}
                  labelElement={<span style={{ color: CLASSIFICATION[u.clearance].color }}>{u.clearance_label}</span>}
                  onClick={() => switchUser(u.username)} />
              ))}
            </Menu>
          }
        >
          <Button minimal icon="user" rightIcon="caret-down" text={state.user ? `${state.user.username}` : "…"} />
        </Popover>
      </div>
    </div>
  );
}
