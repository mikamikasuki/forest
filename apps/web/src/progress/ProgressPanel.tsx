import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { api, formatDate } from "../api";
import { Badge, Button, ErrorBox, Field, useUI, useLoad } from "../ui";
import { useProgress } from "./useProgress";
import type { Fact, Source, Job, SettingsView } from "./types";
import "./progress.css";

export function sourceLink(source: Source) {
  return `/projects/${encodeURIComponent(source.project_id)}/files?source=${encodeURIComponent(JSON.stringify(source))}`;
}
function FactRow({ fact }: { fact: Fact }) {
  return <li className="progress-fact"><div><strong>{fact.label}</strong> <span>{fact.value}</span></div>
    <small>{fact.classification} · {fact.applicability} · {formatDate(fact.observed_at)}</small>
    {fact.sources.map((source, index) => <NavLink key={index} to={sourceLink(source)}>Inspect source</NavLink>)}</li>;
}
export function narrativeDraftAfterSettingsLoad(
  incoming: SettingsView["settings"],
  current: SettingsView["settings"] | null,
  dirty: boolean,
) {
  return dirty && current ? current : incoming;
}
export function narrativeSettingsDiffer(a: SettingsView["settings"], b: SettingsView["settings"]) {
  return a.enabled !== b.enabled
    || a.automatic !== b.automatic
    || a.provider_id !== b.provider_id
    || a.cap_usd !== b.cap_usd
    || a.max_requests !== b.max_requests;
}
export function ProgressPanel({ projectId }: { projectId: string }) {
  const { t, action } = useUI();
  const { snapshot, error } = useProgress(projectId);
  const settings = useLoad<SettingsView | null>(`/projects/${projectId}/reporter-settings`, null);
  const latest = useLoad<Job | null>(`/projects/${projectId}/reports/latest`, null);
  const providers = useLoad<{ id: string; name: string; model: string; status: string }[]>(`/providers`, []);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<SettingsView["settings"] | null>(null);
  const [draftDirty, setDraftDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const updateDraft = (patch: Partial<SettingsView["settings"]>) => {
    if (!draft) return;
    const next = { ...draft, ...patch };
    setDraft(next);
    setDraftDirty(settings.data?.settings ? narrativeSettingsDiffer(next, settings.data.settings) : true);
  };
  useEffect(() => {
    const timer = setInterval(() => { void latest.reload(); }, 5000);
    return () => clearInterval(timer);
  }, [latest.reload]);
  useEffect(() => {
    if (settings.data?.settings) {
      setDraft((current) => narrativeDraftAfterSettingsLoad(settings.data!.settings, current, draftDirty));
    }
  }, [settings.data, draftDirty]);
  useEffect(() => { void settings.reload(); }, [latest.data?.id, latest.data?.status, settings.reload]);
  const sections = [["now", "现在", "Now"], ["attention", "需要处理", "Needs attention"], ["blocked", "受阻", "Blocked"],
    ["recent", "最近变化", "Recent changes"], ["next", "已排队工作", "Next planned work"], ["evidence", "证据与交付", "Evidence and delivery"]] as const;
  return <section className="surface live-progress" aria-label="Live project progress">
    <div className="section-toolbar"><h2>{t("实时进度", "Live progress")}</h2>
      <NavLink className="button" to={`/projects/${projectId}/files`}>{t("浏览来源", "Explore sources")}</NavLink></div>
    <ErrorBox error={error} />
    {!snapshot ? <p>{t("正在建立状态快照…", "Building progress snapshot…")}</p> : <>
      <div className="inline-meta"><Badge status={snapshot.health.state} /><span>Snapshot {snapshot.generation}</span>
        <span>{t("观察于", "Observed")} {formatDate(snapshot.observed_at)}</span><span>{Math.round(snapshot.health.age_seconds || 0)}s old · 2s polling fallback</span></div>
      <p className="muted">{snapshot.health.database_coverage}. Files: {snapshot.health.file_coverage}.</p>
      <p className="muted">{snapshot.health.note}{snapshot.health.history_gap && " Event history gap; current state was rebuilt."}</p>
      <div className="progress-counts">{Object.entries(snapshot.run_counts).map(([status, count]) => <span key={status}>{status}: <strong>{count}</strong></span>)}</div>
      <div className="progress-sections">{sections.map(([key, zh, en]) => <div key={key}><h3>{t(zh, en)}</h3>
        <ul>{snapshot.facts.filter((fact) => fact.section === key).map((fact) => <FactRow key={fact.id} fact={fact} />)}</ul>
        {!snapshot.facts.some((fact) => fact.section === key) && <p className="muted">{t("没有已记录条目", "No recorded items")}</p>}</div>)}</div>
    </>}
    <details open={editing} onToggle={(event) => setEditing(event.currentTarget.open)}><summary>{t("可选叙述器设置", "Optional narrative settings")}</summary>
      <ErrorBox error={settings.error || providers.error} />
      <p>Automatic narration is opt-in. Requests share provider/project limits and the ModelRequest ledger. Local Codex subscription usage has no USD price; the request cap applies.</p>
      {draft && <form onSubmit={(event) => { event.preventDefault(); void action(async () => {
        const value = await api<SettingsView>(`/projects/${projectId}/reporter-settings`, "PATCH", { settings: draft, expected_version: settings.data!.version });
        settings.setData(value); setDraft(value.settings); setDraftDirty(false); setEditing(false);
      }); }}>
        <label><input type="checkbox" checked={draft.enabled} onChange={(e) => updateDraft({ enabled: e.target.checked })} />Enable narrative</label>
        <label><input type="checkbox" checked={draft.automatic} onChange={(e) => updateDraft({ automatic: e.target.checked })} />Automatic meaningful-event refresh</label>
        <Field label="Narrative provider"><select aria-label="Narrative provider" value={draft.provider_id || ""} onChange={(e) => updateDraft({ provider_id: e.target.value || null })}>
          <option value="">Choose provider</option>{providers.data.filter((p) => p.status !== "retired").map((p) => <option key={p.id} value={p.id}>{p.name} · {p.model}</option>)}</select></Field>
        <Field label="Reporting cap (USD)"><input type="number" min="0" max="10000" step="0.01" value={draft.cap_usd} onChange={(e) => updateDraft({ cap_usd: Number(e.target.value) })} /></Field>
        <Field label="Reporting request cap"><input type="number" min="1" max="1000" value={draft.max_requests} onChange={(e) => updateDraft({ max_requests: Number(e.target.value) })} /></Field>
        <Button type="submit">Save narrative settings</Button>
      </form>}
      <p>{settings.data?.requests || 0} reporting requests · estimated ${settings.data?.estimated_usd || 0} · reserved ${settings.data?.reserved_usd || 0}
        {!!settings.data?.unpriced_requests && ` · ${settings.data.unpriced_requests} requests with unknown USD cost`}</p>
    </details>
    <div className="section-toolbar"><h3>{t("来源绑定叙述", "Source-bound narrative")}</h3><Button disabled={!settings.data?.settings.enabled} busy={busy} onClick={async () => {
      setBusy(true); await action(async () => { const job = await api<Job>(`/projects/${projectId}/reports/refresh`, "POST", { request_id: crypto.randomUUID() }); latest.setData(job); await settings.reload(); }); setBusy(false);
    }}>Refresh narrative</Button></div>
    <ErrorBox error={latest.error} />
    {!settings.data?.settings.enabled && <p className="muted">Narrative not enabled/configured. Deterministic progress remains available.</p>}
    {latest.data && <div><Badge status={latest.data.status} /><span> · {latest.data.current ? "Current dependencies" : "Stale dependencies"} · {formatDate(latest.data.created_at)}</span>
      {latest.data.error && <p>{latest.data.error}</p>}
      {latest.data.report && <><p>Model-selected focus: {latest.data.report.focus}. Facts are rendered from snapshot {latest.data.snapshot_id}.</p>
        <ul>{latest.data.facts.map((fact) => <FactRow key={fact.id} fact={fact} />)}</ul></>}</div>}
  </section>;
}
