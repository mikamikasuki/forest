import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { RefreshCw, Send } from "lucide-react";
import type { Graph, Json, Run, RecordItem } from "./api";
import { api, downloadBlob, executionLinks, formatDate, uid } from "./api";
import { requestApi } from "./apiClient";
import {
  Badge,
  Button,
  ErrorBox,
  Field,
  JsonView,
  Tabs,
  useLoad,
  useUI,
} from "./ui";
import {
  acknowledgeAnalysisIntent,
  analysisDraftChange,
  analysisDraftKey,
  analysisReferenceUrl,
  analysisReportMarkdown,
  persistAnalysisDraft,
  readAnalysisDraft,
  retainAnalysisIntent,
  reviewAnalysisContext,
} from "./researchAnalysisDraft";
import type { AnalysisDraft, AnalysisScope } from "./researchAnalysisDraft";

type AnalysisContext = {
  project: Json;
  graph: Graph;
  runs: (Run & { verification?: Json; artifacts?: Json[] })[];
  resources: Record<string, Json[]>;
  materials: Json[];
  execution_dependencies?: Json[];
  tool_executions?: Json[];
  events?: Json[];
  file_revisions?: Json[];
  route_health: Json;
  coverage: Json;
  focus: {
    scope: AnalysisScope;
    node_id?: string;
    branch_id?: string;
    node_ids: string[];
    upstream_node_ids: string[];
    downstream_node_ids: string[];
    run_ids: string[];
  };
  snapshot: { project_revision: number; fingerprint: string };
  provider: {
    available: boolean;
    id?: string;
    name?: string;
    model?: string;
    kind?: string;
    allow_paid?: boolean;
  };
};
type AnalysisHistory = {
  id: string;
  status: string;
  created_at: string;
  error?: string;
  scope: AnalysisScope;
  node_id?: string;
  branch_id?: string;
  question: string;
  project_revision: number;
  report_available: boolean;
};
type AnalysisResult = {
  run_id: string;
  status: string;
  error?: string;
  artifact_error?: string | null;
  report: Json | null;
  citations: Json[];
  literature_searches: Json[];
  snapshot: { project_revision: number; fingerprint: string };
  current: boolean;
  stale_reasons: string[];
  artifacts: Json;
};
const activeStates = [
  "queued",
  "running",
  "pausing",
  "paused",
  "waiting",
  "waiting_input",
  "budget_exhausted",
];

function BoundReferences({
  projectId,
  value,
}: {
  projectId: string;
  value: Json;
}) {
  const { t } = useUI();
  return (
    <div className="analysis-bound-references">
      {(value.node_ids || (value.node_id ? [value.node_id] : [])).map(
        (id: string) => (
          <NavLink
            key={`node-${id}`}
            className="text-link"
            to={`/projects/${projectId}/workspace?analysis=1&analysis_scope=node&node=${encodeURIComponent(id)}`}
          >
            {t("节点", "Node")} <code>{id}</code>
          </NavLink>
        ),
      )}
      {(value.run_ids || []).map((id: string) => (
        <NavLink
          key={`run-${id}`}
          className="text-link"
          to={`/projects/${projectId}/runs?run=${encodeURIComponent(id)}`}
        >
          {t("运行", "Run")} <code>{id}</code>
        </NavLink>
      ))}
      {(value.citation_ids || []).map((id: string) => (
        <a
          key={`citation-${id}`}
          className="text-link"
          href={`#analysis-citation-${encodeURIComponent(id)}`}
        >
          {t("参考来源", "Reference")} <code>{id}</code>
        </a>
      ))}
    </div>
  );
}

function ContextPreview({
  context,
  projectId,
  scope,
}: {
  context: AnalysisContext;
  projectId: string;
  scope: AnalysisScope;
}) {
  const { t } = useUI();
  const [allRecords, setAllRecords] = useState(false);
  const focusIds = new Set(context.focus.node_ids || []);
  const nodes = context.graph.nodes.filter(
    (node) => scope === "project" || focusIds.has(node.id),
  );
  const runIds = new Set(context.focus.run_ids || []);
  const runs = context.runs.filter(
    (run) => scope === "project" || runIds.has(run.id),
  );
  const node = context.graph.nodes.find(
    (node) => node.id === context.focus.node_id,
  );
  const branch = context.graph.branches.find(
    (branch) => branch.id === context.focus.branch_id,
  );
  const executionDependencies =
    context.execution_dependencies ??
    executionLinks(context.graph).filter((edge) =>
      ["depends_on", "consumes"].includes(edge.relation),
    );
  return (
    <section
      className="analysis-context-note"
      aria-label={t("已保存的分析上下文", "Saved analysis context")}
    >
      <strong>{node?.title || branch?.name || context.project.name}</strong>
      <p>
        {t("已保存项目版本", "Saved project revision")}{" "}
        {context.snapshot.project_revision} · {nodes.length}{" "}
        {t("节点", "nodes")} · {runs.length} {t("运行", "runs")}
      </p>
      {node && (
        <p>
          <code>{node.id}</code> · {node.type} · r{node.revision} ·{" "}
          <Badge status={node.execution_status} />{" "}
          <Badge status={node.research_status} />
        </p>
      )}
      {branch && (
        <p>
          <code>{branch.id}</code> · <Badge status={branch.status} />
        </p>
      )}
      <details>
        <summary>
          {t("研究目标与覆盖范围", "Goal and context coverage")}
        </summary>
        <p>{context.project.goal}</p>
        <JsonView value={context.coverage} />
        {scope !== "project" && <JsonView value={context.focus} />}
        <p>
          {t(
            "材料预览保留文件位置；读取文件内容属于后续模型分析。",
            "This preview retains material locations; reading file contents belongs to the requested model analysis.",
          )}
        </p>
      </details>
      <details>
        <summary>
          {t("实际执行与结果记录", "Actual execution and result records")} (
          {runs.length})
        </summary>
        {(allRecords ? runs : runs.slice(-8)).map((run) => (
          <article key={run.id} className="analysis-finding">
            <strong>
              {context.graph.nodes.find((node) => node.id === run.node_id)
                ?.title || run.kind}
            </strong>
            <p>
              <Badge status={run.status} /> · {run.kind} ·{" "}
              {t("节点版本", "Node revision")} {run.node_revision ?? "—"}
            </p>
            <BoundReferences
              projectId={projectId}
              value={{
                run_ids: [run.id],
                node_ids: run.node_id ? [run.node_id] : [],
              }}
            />
            {run.error && <p role="status">{run.error}</p>}
            {Object.keys(run.metrics || {}).length > 0 && (
              <details>
                <summary>{t("已记录指标", "Recorded metrics")}</summary>
                <JsonView value={run.metrics} />
              </details>
            )}
            {run.verification && (
              <details>
                <summary>
                  {t("来源验证回执", "Source verification receipt")}
                </summary>
                <JsonView value={run.verification} />
              </details>
            )}
          </article>
        ))}
        {!runs.length && (
          <p>{t("此范围尚无运行记录。", "No run records in this scope.")}</p>
        )}
        {runs.length > 8 && !allRecords && (
          <Button onClick={() => setAllRecords(true)}>
            {t("查看全部记录", "Show all records")}
          </Button>
        )}
      </details>
      <details>
        <summary>{t("研究路线状态记录", "Recorded path health")}</summary>
        <JsonView value={context.route_health} />
      </details>
      <details>
        <summary>
          {t("路线、节点与依赖详情", "Paths, nodes and dependency details")}
        </summary>
        {context.graph.branches.map((path) => {
          const pathNodes = context.graph.nodes.filter(
            (node) => node.branch_id === path.id,
          );
          const pathRuns = context.runs.filter(
            (run) => run.branch_id === path.id,
          );
          const states = pathRuns.reduce<Record<string, number>>(
            (counts, run) => ({
              ...counts,
              [run.status]: (counts[run.status] || 0) + 1,
            }),
            {},
          );
          return (
            <article className="analysis-finding" key={path.id}>
              <strong>{path.name}</strong>
              <p>
                <Badge status={path.status} /> · {pathNodes.length}{" "}
                {t("节点", "nodes")} · {pathRuns.length} {t("运行", "runs")}
              </p>
              <NavLink
                className="text-link"
                to={`/projects/${projectId}/workspace?analysis=1&analysis_scope=branch&branch=${encodeURIComponent(path.id)}`}
              >
                <code>{path.id}</code>
              </NavLink>
              <JsonView value={states} />
            </article>
          );
        })}
        {nodes.map((node) => {
          const upstream = executionDependencies
            .filter((edge) => edge.target === node.id)
            .map((edge) => edge.source);
          const downstream = executionDependencies
            .filter((edge) => edge.source === node.id)
            .map((edge) => edge.target);
          return (
            <details key={node.id}>
              <summary>
                {node.title} · r{node.revision}
              </summary>
              <BoundReferences
                projectId={projectId}
                value={{ node_ids: [node.id] }}
              />
              <p>
                <Badge status={node.execution_status} />{" "}
                <Badge status={node.research_status} />{" "}
                <Badge status={node.deliverable_status} />
              </p>
              <p>{node.instructions}</p>
              <h4>{t("上游依赖", "Upstream dependencies")}</h4>
              <BoundReferences
                projectId={projectId}
                value={{ node_ids: upstream }}
              />
              <h4>{t("下游影响", "Downstream nodes")}</h4>
              <BoundReferences
                projectId={projectId}
                value={{ node_ids: downstream }}
              />
              <details>
                <summary>
                  {t(
                    "完整配置、输入输出与评论",
                    "Complete configuration, inputs, outputs and comments",
                  )}
                </summary>
                <JsonView value={node} />
              </details>
            </details>
          );
        })}
        <details>
          <summary>
            {t(
              "完整保存的图谱与依赖关系",
              "Complete saved graph and dependencies",
            )}
          </summary>
          <JsonView
            value={{
              graph: context.graph,
              execution_dependencies: executionDependencies,
            }}
          />
        </details>
      </details>
      <details>
        <summary>
          {t(
            "工具执行、事件与文件版本",
            "Tool execution, events and file versions",
          )}
        </summary>
        {(
          [
            [
              t(
                "执行依赖（含隐式绑定）",
                "Execution dependencies, including implicit bindings",
              ),
              executionDependencies,
            ],
            [
              t("工具执行回执", "Tool execution receipts"),
              context.tool_executions || [],
            ],
            [t("已记录事件", "Recorded events"), context.events || []],
            [
              t("文件版本记录", "File revision records"),
              context.file_revisions || [],
            ],
          ] as [string, Json[]][]
        ).map(([label, records]) => (
          <details key={label}>
            <summary>
              {label} ({records.length})
            </summary>
            <JsonView value={records} />
          </details>
        ))}
      </details>
      <details>
        <summary>
          {t(
            "论文、文献与研究资源",
            "Papers, literature and research resources",
          )}
        </summary>
        {Object.entries(context.resources || {}).map(([kind, resources]) => (
          <details key={kind}>
            <summary>
              {kind} ({resources.length})
            </summary>
            <JsonView value={resources} />
          </details>
        ))}
        <details>
          <summary>
            {t("材料文件位置", "Material file locations")} (
            {context.materials?.length || 0})
          </summary>
          <JsonView value={context.materials || []} />
        </details>
      </details>
    </section>
  );
}

function AnalysisReport({
  projectId,
  result,
}: {
  projectId: string;
  result: AnalysisResult;
}) {
  const { t } = useUI();
  const [actionError, setActionError] = useState("");
  const [copied, setCopied] = useState(false);
  const [imports, setImports] = useState<Record<string, RecordItem>>({});
  const [importing, setImporting] = useState<string[]>([]);
  const importLocks = useRef(new Set<string>());
  const report = result.report;
  if (!report || result.artifact_error) return null;
  return (
    <article
      className="analysis-report"
      aria-label={t("已保存的研究分析", "Saved research analysis")}
    >
      <header className="analysis-report-heading">
        <h2>{t("研究分析", "Research analysis")}</h2>
        <Badge status={result.status} />
        <p>
          <code>{result.run_id}</code> ·{" "}
          {t("分析使用的项目版本", "Analyzed project revision")}{" "}
          {result.snapshot.project_revision}
        </p>
      </header>
      {!result.current && (
        <p className="analysis-stale" role="status">
          {t(
            "已保存分析的上下文发生变化。",
            "The saved analysis context has changed.",
          )}{" "}
          {(result.stale_reasons || []).join(" · ")}
        </p>
      )}
      <p>
        {t(
          "模型建议与判断；实验验证与科学结论以各自来源为准。",
          "Model advice and judgments; experiment verification and scientific conclusions retain their own source evidence.",
        )}
      </p>
      <div className="inline-actions">
        <Button
          onClick={async () => {
            setActionError("");
            try {
              await navigator.clipboard.writeText(
                analysisReportMarkdown(projectId, result),
              );
              setCopied(true);
            } catch (failure) {
              setActionError((failure as Error).message);
            }
          }}
        >
          {copied
            ? t("已复制报告", "Report copied")
            : t("复制报告", "Copy report")}
        </Button>
        <Button
          onClick={() =>
            downloadBlob(
              new Blob([analysisReportMarkdown(projectId, result)], {
                type: "text/markdown;charset=utf-8",
              }),
              `research-analysis-${result.run_id}.md`,
            )
          }
        >
          {t("导出 Markdown", "Export Markdown")}
        </Button>
        <Button
          onClick={() =>
            downloadBlob(
              new Blob(
                [JSON.stringify({ project_id: projectId, ...result }, null, 2)],
                { type: "application/json" },
              ),
              `research-analysis-${result.run_id}.json`,
            )
          }
        >
          {t("导出 JSON", "Export JSON")}
        </Button>
      </div>
      <ErrorBox error={actionError} />
      <p className="analysis-summary">{report.summary}</p>
      <section className="analysis-report-section">
        <h3>{t("已做实验及存在问题", "Experiments and issues")}</h3>
        {(report.experiments || []).map((experiment: Json, index: number) => (
          <article className="analysis-finding" key={index}>
            <h4>{experiment.what_was_done}</h4>
            <p>{experiment.result}</p>
            <p>{experiment.problems}</p>
            <BoundReferences projectId={projectId} value={experiment} />
          </article>
        ))}
      </section>
      <section className="analysis-report-section">
        <h3>{t("研究判断", "Findings")}</h3>
        {(report.findings || []).map((finding: Json, index: number) => (
          <article className="analysis-finding" key={index}>
            <h4>{finding.title}</h4>
            <p>
              <Badge status={finding.severity} /> {finding.evidence_label}
            </p>
            <p>{finding.detail}</p>
            <BoundReferences projectId={projectId} value={finding} />
          </article>
        ))}
      </section>
      <section className="analysis-report-section">
        <h3>{t("建议的下一步", "Recommended next steps")}</h3>
        {(report.recommendations || []).map(
          (recommendation: Json, index: number) => (
            <article className="analysis-finding" key={index}>
              <h4>{recommendation.title}</h4>
              <p>
                {recommendation.priority} · {recommendation.kind}
              </p>
              <p>{recommendation.action}</p>
              <p>{recommendation.rationale}</p>
              <BoundReferences projectId={projectId} value={recommendation} />
              {recommendation.judgment && (
                <details>
                  <summary>
                    {t("判断依据与成功条件", "Judgment and success conditions")}
                  </summary>
                  <JsonView value={recommendation.judgment} />
                </details>
              )}
            </article>
          ),
        )}
      </section>
      {!!report.limitations?.length && (
        <section className="analysis-report-section">
          <h3>{t("证据与阅读限制", "Evidence and reading limits")}</h3>
          <ul>
            {report.limitations.map((value: string, index: number) => (
              <li key={index}>{value}</li>
            ))}
          </ul>
        </section>
      )}
      <section className="analysis-citation-list">
        <h3>{t("文献与引用来源", "Literature and citations")}</h3>
        {(result.citations || []).map((citation) => {
          const url =
            analysisReferenceUrl(citation.url) ||
            analysisReferenceUrl(
              citation.doi
                ? `https://doi.org/${encodeURI(citation.doi)}`
                : undefined,
            );
          return (
            <article
              className="analysis-finding"
              key={citation.id}
              id={`analysis-citation-${encodeURIComponent(citation.id)}`}
            >
              <h4>
                {url ? (
                  <a
                    className="text-link"
                    href={url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {citation.title}
                  </a>
                ) : (
                  citation.title
                )}
              </h4>
              <p>
                {Array.isArray(citation.authors)
                  ? citation.authors.join(", ")
                  : citation.authors}{" "}
                · {citation.year ?? "—"}
              </p>
              <p>
                {citation.source} · {t("已读范围", "Read scope")}:{" "}
                {citation.read_scope}
              </p>
              <code>{citation.id}</code>
              {citation.abstract && (
                <details>
                  <summary>{t("保存的摘要", "Saved abstract")}</summary>
                  <p>{citation.abstract}</p>
                </details>
              )}
              {imports[citation.id] ? (
                <p role="status">
                  {t("已保存文献记录", "Saved library record")}{" "}
                  <code>{imports[citation.id].id}</code>{" "}
                  <NavLink
                    className="text-link"
                    to={`/projects/${projectId}/library`}
                  >
                    {t("打开文献库", "Open library")}
                  </NavLink>
                </p>
              ) : (
                <Button
                  busy={importing.includes(citation.id)}
                  onClick={async () => {
                    if (importLocks.current.has(citation.id)) return;
                    importLocks.current.add(citation.id);
                    setImporting((current) => [...current, citation.id]);
                    setActionError("");
                    try {
                      const saved = await requestApi(
                        "/api/library/import",
                        "post",
                        {
                          body: {
                            project_id: projectId,
                            paper: {
                              ...citation,
                              origin: {
                                kind: "research_analysis",
                                run_id: result.run_id,
                                citation_id: citation.id,
                              },
                              reading_scope: citation.read_scope,
                            },
                          },
                        },
                      );
                      const recordId =
                        "id" in saved ? saved.id : saved.records?.[0]?.id;
                      const records = await api<RecordItem[]>(
                        `/library?project_id=${encodeURIComponent(projectId)}`,
                      );
                      const actual = records.find(
                        (record) =>
                          record.id === recordId &&
                          record.project_id === projectId,
                      );
                      if (!actual)
                        throw new Error(
                          t(
                            "已接收导入，但尚未读回此文献记录。",
                            "Import was accepted, but the library record could not be read back.",
                          ),
                        );
                      setImports((current) => ({
                        ...current,
                        [citation.id]: actual,
                      }));
                      window.dispatchEvent(new Event("forest-refresh"));
                    } catch (failure) {
                      setActionError((failure as Error).message);
                    } finally {
                      importLocks.current.delete(citation.id);
                      setImporting((current) =>
                        current.filter((id) => id !== citation.id),
                      );
                    }
                  }}
                >
                  {t("保存引用到文献库", "Save citation to library")}
                </Button>
              )}
            </article>
          );
        })}
        {!result.citations?.length && (
          <p>
            {t(
              "此分析未返回文献引用。",
              "This analysis returned no literature citations.",
            )}
          </p>
        )}
        {!!result.literature_searches?.length && (
          <details>
            <summary>
              {t("实际文献检索回执", "Literature search receipts")}
            </summary>
            <JsonView value={result.literature_searches} />
          </details>
        )}
      </section>
      <details>
        <summary>
          {t("完整报告与来源文件", "Complete report and source artifacts")}
        </summary>
        <JsonView
          value={{
            report,
            coverage: report.coverage,
            snapshot: result.snapshot,
            artifacts: result.artifacts,
          }}
        />
      </details>
    </article>
  );
}

function AnalysisSession({
  projectId,
  scope,
  nodeId,
  branchId,
  context,
  history,
}: {
  projectId: string;
  scope: AnalysisScope;
  nodeId: string;
  branchId: string;
  context: AnalysisContext;
  history: AnalysisHistory[];
}) {
  const { t, lang } = useUI();
  const key = analysisDraftKey(projectId, scope, nodeId, branchId);
  const [draft, setDraft] = useState<AnalysisDraft>(() =>
    readAnalysisDraft(key),
  );
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const mounted = useRef(true),
    lock = useRef(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [showAllHistory, setShowAllHistory] = useState(false);
  const [acceptedRun, setAcceptedRun] = useState<AnalysisHistory | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const edit = (
    next: AnalysisDraft | ((current: AnalysisDraft) => AnalysisDraft),
  ) => {
    const updated = typeof next === "function" ? next(draftRef.current) : next;
    draftRef.current = updated;
    persistAnalysisDraft(key, updated);
    setDraft(updated);
  };
  useEffect(() => {
    if (draftRef.current.baseRevision === null)
      edit((current) => reviewAnalysisContext(current, context));
  }, [
    context.snapshot.project_revision,
    context.snapshot.fingerprint,
    context.provider.id,
  ]);
  const scopedHistory = history.filter(
    (run) =>
      run.scope === scope &&
      (scope !== "node" || run.node_id === nodeId) &&
      (scope !== "branch" || run.branch_id === branchId),
  );
  const selectedId = draft.lastRunId || scopedHistory[0]?.id || "";
  const selected =
    scopedHistory.find((run) => run.id === selectedId) ||
    (acceptedRun?.id === selectedId ? acceptedRun : undefined);
  const detail = useLoad<AnalysisResult | null>(
    selected ? `/runs/${selected.id}/research-analysis` : null,
    null,
  );
  const result = detail.data?.run_id === selected?.id ? detail.data : null;
  const changed = analysisDraftChange(draft, context);
  const retryProvider = changed?.providerChanged;
  useEffect(() => {
    if (!selected) return;
    const refresh = () => void detail.reload();
    const timer = activeStates.includes(selected.status)
      ? setInterval(refresh, 3000)
      : undefined;
    window.addEventListener("forest-refresh", refresh);
    return () => {
      if (timer) clearInterval(timer);
      window.removeEventListener("forest-refresh", refresh);
    };
  }, [selected?.id, selected?.status, detail.reload]);
  return (
    <>
      <form
        className="analysis-request-form"
        onSubmit={(event) => event.preventDefault()}
      >
        <Field label={t("分析问题", "Analysis question")}>
          <textarea
            rows={4}
            value={draft.question}
            onChange={(event) =>
              edit((current) => ({ ...current, question: event.target.value }))
            }
            placeholder={t(
              "希望解释哪些实验、问题或下一步？",
              "Which experiments, issues or next steps should be examined?",
            )}
          />
        </Field>
        <details>
          <summary>{t("文献检索", "Literature search")}</summary>
          <Field label={t("文献检索词（可选）", "Literature query (optional)")}>
            <input
              value={draft.literatureQuery}
              onChange={(event) =>
                edit((current) => ({
                  ...current,
                  literatureQuery: event.target.value,
                }))
              }
            />
          </Field>
          <div className="analysis-literature-sources">
            {(["crossref", "arxiv"] as const).map((source) => (
              <label key={source}>
                <input
                  type="checkbox"
                  checked={draft.literatureSources.includes(source)}
                  onChange={(event) =>
                    edit((current) => ({
                      ...current,
                      literatureSources: event.target.checked
                        ? [...current.literatureSources, source]
                        : current.literatureSources.filter(
                            (value) => value !== source,
                          ),
                    }))
                  }
                />{" "}
                {source === "crossref" ? "Crossref" : "arXiv"}
              </label>
            ))}
          </div>
        </details>
        <p>
          {context.provider.available
            ? `${context.provider.name || context.provider.kind || ""} · ${context.provider.model || ""}`
            : t(
                "尚未连接可用于分析的模型。已保存上下文仍可阅读。",
                "No model is connected for analysis. Saved context remains readable.",
              )}
        </p>
        {!context.provider.available && (
          <NavLink className="text-link" to="/settings">
            {t("连接模型", "Connect a model")}
          </NavLink>
        )}
        <p>
          {t("草稿使用的项目版本", "Draft project revision")}{" "}
          {draft.baseRevision ?? "—"}
        </p>
        {changed && <p role="status">{t(changed.zh, changed.en)}</p>}
        <div className="inline-actions">
          <Button
            disabled={busy}
            onClick={() =>
              edit((current) =>
                reviewAnalysisContext(
                  { ...current, intent: undefined },
                  context,
                ),
              )
            }
          >
            <RefreshCw size={14} />
            {t("使用当前上下文", "Use current context")}
          </Button>
          <Button
            className="primary"
            busy={busy}
            disabled={
              draft.baseRevision === null ||
              (retryProvider
                ? !draft.intent
                : !context.provider.available || !draft.providerId)
            }
            onClick={async () => {
              const current = draftRef.current;
              if (
                lock.current ||
                current.baseRevision === null ||
                (retryProvider && !current.intent)
              )
                return;
              lock.current = true;
              setBusy(true);
              setError("");
              const next = retryProvider
                ? current
                : retainAnalysisIntent(
                    current,
                    {
                      expected_revision: current.baseRevision!,
                      expected_fingerprint: current.baseFingerprint,
                      scope,
                      ...(scope === "node" ? { node_id: nodeId } : {}),
                      ...(scope === "branch" ? { branch_id: branchId } : {}),
                      question: current.question,
                      language: lang === "zh" ? "zh" : "en",
                      ...(current.literatureQuery.trim()
                        ? { literature_query: current.literatureQuery.trim() }
                        : {}),
                      literature_sources: current.literatureSources,
                      literature_limit: 6,
                      timeout: 180,
                      provider_id: current.providerId,
                    },
                    uid,
                  );
              edit(next);
              const intent = next.intent!;
              try {
                const run = await requestApi(
                  "/api/projects/{ident}/research/analysis",
                  "post",
                  {
                    path: { ident: projectId },
                    body: { ...intent.payload, request_id: intent.requestId },
                  },
                );
                if (
                  run.project_id !== projectId ||
                  run.kind !== "research_analysis"
                )
                  throw new Error(
                    t(
                      "分析回执与所请求项目不匹配。",
                      "The analysis receipt does not match the requested project.",
                    ),
                  );
                const latest = mounted.current
                  ? draftRef.current
                  : readAnalysisDraft(key);
                const acknowledged = acknowledgeAnalysisIntent(
                  latest,
                  intent.requestId,
                  run.id,
                  current.lastRunId,
                );
                persistAnalysisDraft(key, acknowledged);
                if (mounted.current) {
                  setAcceptedRun({
                    id: run.id,
                    status: run.status,
                    created_at: run.created_at || "",
                    scope,
                    node_id: scope === "node" ? nodeId : undefined,
                    branch_id: scope === "branch" ? branchId : undefined,
                    question: intent.payload.question,
                    project_revision: intent.payload.expected_revision,
                    report_available: false,
                  });
                  edit(acknowledged);
                }
                window.dispatchEvent(new Event("forest-refresh"));
              } catch (failure) {
                if (mounted.current) setError((failure as Error).message);
              } finally {
                lock.current = false;
                if (mounted.current) setBusy(false);
              }
            }}
          >
            <Send size={14} />
            {retryProvider && draft.intent
              ? t("使用原服务商重试", "Retry with previous provider")
              : t("请求 AI 分析", "Request AI analysis")}
          </Button>
        </div>
        <ErrorBox error={error} />
        {draft.intent && !busy && (
          <p role="status">
            {t(
              "保留此提交标识；再次请求相同内容会恢复同一意图。",
              "This submission identity is retained; requesting the same content recovers the same intent.",
            )}{" "}
            <code>{draft.intent.requestId}</code>
          </p>
        )}
      </form>
      {selected && (
        <section className="analysis-run-receipt">
          <strong>{t("分析运行记录", "Analysis run record")}</strong>
          <p>
            <Badge status={result?.status || selected.status} /> ·{" "}
            {formatDate(selected.created_at)}
          </p>
          <NavLink
            className="text-link"
            to={`/projects/${projectId}/runs?run=${encodeURIComponent(selected.id)}`}
          >
            <code>{selected.id}</code>
          </NavLink>
          <p>{selected.question}</p>
          <ErrorBox
            error={
              result?.artifact_error ||
              result?.error ||
              selected.error ||
              detail.error
            }
            retry={detail.reload}
          />
          {detail.loading && (
            <p role="status">
              {t("正在读取已保存分析…", "Reading saved analysis…")}
            </p>
          )}
          {!detail.loading && !result?.report && (
            <p>
              {activeStates.includes(result?.status || selected.status)
                ? t(
                    "等待实际分析结果。",
                    "Waiting for the actual analysis result.",
                  )
                : t(
                    "此运行没有可用的分析报告。",
                    "This run has no available analysis report.",
                  )}
            </p>
          )}
        </section>
      )}
      {draft.lastRunId && !selected && (
        <p role="status">
          {t(
            "所选分析记录在当前项目中不可用。",
            "Selected analysis is unavailable in this project.",
          )}{" "}
          <code>{draft.lastRunId}</code>
        </p>
      )}
      {result?.report && !result.artifact_error && (
        <AnalysisReport
          key={result.run_id}
          projectId={projectId}
          result={result}
        />
      )}
      <details className="analysis-history">
        <summary>
          {t("此范围的分析历史", "Analysis history for this scope")} (
          {scopedHistory.length})
        </summary>
        {(showAllHistory ? scopedHistory : scopedHistory.slice(0, 8)).map(
          (run) => (
            <button
              key={run.id}
              className={selectedId === run.id ? "active" : ""}
              onClick={() =>
                edit((current) => ({ ...current, lastRunId: run.id }))
              }
            >
              <span>{run.question || t("研究分析", "Research analysis")}</span>
              <Badge status={run.status} />
              <code>{run.id}</code>
              <small>
                {formatDate(run.created_at)} · r{run.project_revision}
              </small>
            </button>
          ),
        )}
        {scopedHistory.length > 8 && !showAllHistory && (
          <Button onClick={() => setShowAllHistory(true)}>
            {t("查看全部分析", "Show all analyses")}
          </Button>
        )}
      </details>
    </>
  );
}

export function ResearchAnalysisPanel({
  projectId,
  scope,
  nodeId,
  branchId,
  onScopeChange,
  onNodeChange,
  onBranchChange,
  onEditNode,
}: {
  projectId: string;
  scope: AnalysisScope;
  nodeId: string;
  branchId: string;
  onScopeChange: (scope: AnalysisScope) => void;
  onNodeChange: (nodeId: string) => void;
  onBranchChange: (branchId: string) => void;
  onEditNode: () => void;
}) {
  const { t } = useUI();
  const missingTarget =
    (scope === "node" && !nodeId) || (scope === "branch" && !branchId);
  const query = new URLSearchParams({
    scope: missingTarget ? "project" : scope,
  });
  if (!missingTarget && scope === "node") query.set("node_id", nodeId);
  if (!missingTarget && scope === "branch") query.set("branch_id", branchId);
  const context = useLoad<AnalysisContext | null>(
    `/projects/${projectId}/research/analysis/context?${query}`,
    null,
  );
  const history = useLoad<{ runs: AnalysisHistory[] }>(
    `/projects/${projectId}/research/analysis`,
    { runs: [] },
  );
  useEffect(() => {
    const refresh = () => {
      void context.reload();
      void history.reload();
    };
    window.addEventListener("forest-refresh", refresh);
    const timer = setInterval(() => {
      if (history.data.runs.some((run) => activeStates.includes(run.status)))
        refresh();
    }, 3000);
    return () => {
      window.removeEventListener("forest-refresh", refresh);
      clearInterval(timer);
    };
  }, [context.reload, history.reload, history.data.runs]);
  const resolvedScope = missingTarget ? "project" : scope;
  const actual =
    context.data?.project.id === projectId &&
    context.data.focus.scope === resolvedScope &&
    (resolvedScope !== "node" || context.data.focus.node_id === nodeId) &&
    (resolvedScope !== "branch" || context.data.focus.branch_id === branchId)
      ? context.data
      : null;
  return (
    <div className="research-analysis-panel">
      <div className="analysis-panel-intro">
        <h2>{t("研究分析", "Research analysis")}</h2>
        <p>
          {t(
            "读取已保存的实验、问题与来源，按需请求模型分析。",
            "Read saved experiments, issues and sources, then request model analysis when needed.",
          )}
        </p>
      </div>
      <div className="analysis-scope-row">
        <Tabs
          panelId="analysis-scope-tabs"
          value={scope}
          onChange={(value) => onScopeChange(value as AnalysisScope)}
          items={[
            { id: "project", label: t("项目", "Project") },
            { id: "node", label: t("节点", "Node") },
            { id: "branch", label: t("路线", "Path") },
          ]}
        />
      </div>
      <ErrorBox error={context.error} retry={context.reload} />
      {context.loading && !actual && (
        <p role="status">
          {t("正在读取真实项目上下文…", "Reading actual project context…")}
        </p>
      )}
      {actual && (
        <>
          {scope === "node" && (
            <div className="analysis-target-picker">
              <Field label={t("分析节点", "Analysis node")}>
                <select
                  value={nodeId}
                  onChange={(event) => onNodeChange(event.target.value)}
                >
                  <option value="">
                    {t("选择已保存节点", "Choose a saved node")}
                  </option>
                  {actual.graph.nodes.map((node) => (
                    <option key={node.id} value={node.id}>
                      {node.title} · r{node.revision}
                    </option>
                  ))}
                </select>
              </Field>
              <Button
                disabled={
                  !actual.graph.nodes.some((node) => node.id === nodeId)
                }
                onClick={onEditNode}
              >
                {t("编辑所选节点", "Edit selected node")}
              </Button>
            </div>
          )}
          {scope === "branch" && (
            <Field label={t("分析路线", "Analysis path")}>
              <select
                value={branchId}
                onChange={(event) => onBranchChange(event.target.value)}
              >
                <option value="">
                  {t("选择已保存路线", "Choose a saved path")}
                </option>
                {actual.graph.branches.map((branch) => (
                  <option key={branch.id} value={branch.id}>
                    {branch.name} · {branch.status}
                  </option>
                ))}
              </select>
            </Field>
          )}
          {missingTarget ? (
            <p role="status">
              {t(
                "选择一个已保存目标以预览和分析；选择本身不会请求模型。",
                "Choose a saved target to preview and analyze; selecting it does not request a model.",
              )}
            </p>
          ) : (
            <ContextPreview
              key={`${scope}:${nodeId}:${branchId}`}
              context={actual}
              projectId={projectId}
              scope={scope}
            />
          )}
          {!missingTarget && (
            <AnalysisSession
              key={analysisDraftKey(projectId, scope, nodeId, branchId)}
              projectId={projectId}
              scope={scope}
              nodeId={nodeId}
              branchId={branchId}
              context={actual}
              history={history.data.runs}
            />
          )}
        </>
      )}
      <ErrorBox error={history.error} retry={history.reload} />
    </div>
  );
}
