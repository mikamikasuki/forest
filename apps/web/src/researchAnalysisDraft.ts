export type AnalysisScope = "project" | "node" | "branch";
export type AnalysisPayload = {
  expected_revision: number;
  expected_fingerprint?: string;
  scope: AnalysisScope;
  node_id?: string;
  branch_id?: string;
  question: string;
  language: "zh" | "en";
  literature_query?: string;
  literature_sources: ("crossref" | "arxiv")[];
  literature_limit: number;
  timeout: number;
  provider_id?: string;
};
export type AnalysisDraft = {
  question: string;
  literatureQuery: string;
  literatureSources: ("crossref" | "arxiv")[];
  baseRevision: number | null;
  baseFingerprint: string;
  providerId: string;
  providerName?: string;
  lastRunId: string;
  intent?: { serialized: string; requestId: string; payload: AnalysisPayload };
};

type DraftContext = {
  snapshot: { project_revision: number; fingerprint: string };
  provider: { id?: string; name?: string };
};

export function reviewAnalysisContext(
  draft: AnalysisDraft,
  { snapshot, provider }: DraftContext,
): AnalysisDraft {
  return {
    ...draft,
    baseRevision: snapshot.project_revision,
    baseFingerprint: snapshot.fingerprint,
    providerId: provider.id || "",
    providerName: provider.name,
  };
}

export function analysisDraftChange(
  draft: AnalysisDraft,
  { snapshot, provider }: DraftContext,
) {
  if (draft.baseRevision === null) return;
  if (draft.providerId !== (provider.id || "")) {
    const previous =
      draft.providerName ||
      draft.intent?.payload.provider_id ||
      draft.providerId;
    return {
      providerChanged: true,
      zh: draft.intent
        ? `分析服务商已更改。重试会向 ${previous} 重新发送上一次请求。使用当前上下文会开始新的提交。`
        : "分析服务商已更改，请使用当前上下文。",
      en: draft.intent
        ? `Provider changed. Retry sends the original request to ${previous}. Use current context to start a new submission.`
        : "Provider changed. Use current context.",
    };
  }
  if (
    draft.baseRevision !== snapshot.project_revision ||
    draft.baseFingerprint !== snapshot.fingerprint
  )
    return {
      providerChanged: false,
      zh: "上下文已改变；当前草稿保留原版本。",
      en: "Context changed; this draft retains its original revision.",
    };
}

export function analysisDraftKey(
  projectId: string,
  scope: AnalysisScope,
  nodeId: string,
  branchId: string,
) {
  return `forest-analysis-draft:${JSON.stringify([
    projectId,
    scope,
    scope === "node" ? nodeId : scope === "branch" ? branchId : "",
  ])}`;
}
export function readAnalysisDraft(key: string): AnalysisDraft {
  const initial: AnalysisDraft = {
    question: "",
    literatureQuery: "",
    literatureSources: ["crossref", "arxiv"],
    baseRevision: null,
    baseFingerprint: "",
    providerId: "",
    lastRunId: "",
  };
  try {
    const stored = JSON.parse(sessionStorage.getItem(key) || "null");
    if (!stored || typeof stored !== "object" || Array.isArray(stored))
      return initial;
    return {
      ...initial,
      ...stored,
      literatureSources: Array.isArray(stored.literatureSources)
        ? stored.literatureSources.filter((value: string) =>
            ["crossref", "arxiv"].includes(value),
          )
        : initial.literatureSources,
    };
  } catch {
    return initial;
  }
}
export function persistAnalysisDraft(key: string, draft: AnalysisDraft) {
  try {
    sessionStorage.setItem(key, JSON.stringify(draft));
  } catch {
    // The mounted editor still retains the complete draft and request identity.
  }
}
export function retainAnalysisIntent(
  draft: AnalysisDraft,
  payload: AnalysisPayload,
  createId: () => string,
): AnalysisDraft {
  const serialized = JSON.stringify(payload);
  if (draft.intent?.serialized === serialized) return draft;
  return {
    ...draft,
    intent: { serialized, requestId: createId(), payload },
  };
}
export function acknowledgeAnalysisIntent(
  draft: AnalysisDraft,
  requestId: string,
  runId: string,
  submittedRunId = draft.lastRunId,
): AnalysisDraft {
  return {
    ...draft,
    lastRunId: draft.lastRunId === submittedRunId ? runId : draft.lastRunId,
    ...(draft.intent?.requestId === requestId ? { intent: undefined } : {}),
  };
}

export function analysisReferenceUrl(value?: string) {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}

export function analysisReportMarkdown(
  projectId: string,
  result: {
    run_id: string;
    snapshot: { project_revision: number; fingerprint: string };
    current: boolean;
    stale_reasons: string[];
    report: Record<string, any> | null;
    citations: Record<string, any>[];
    literature_searches: Record<string, any>[];
    artifacts: Record<string, any>;
  },
) {
  const report = result.report;
  if (!report) return "";
  const refs = (value: Record<string, any>) =>
    [
      ...(value.node_ids || (value.node_id ? [value.node_id] : [])).map(
        (id: string) => `Node: ${id}`,
      ),
      ...(value.run_ids || []).map((id: string) => `Run: ${id}`),
      ...(value.citation_ids || []).map((id: string) => `Citation: ${id}`),
    ].join("\n");
  return [
    "# Research analysis",
    `Project: ${projectId}`,
    `Run: ${result.run_id}`,
    `Project revision: ${result.snapshot.project_revision}`,
    `Context fingerprint: ${result.snapshot.fingerprint}`,
    `Context current: ${result.current}`,
    ...(result.stale_reasons || []).map(
      (reason) => `Context change: ${reason}`,
    ),
    "Model advice and judgments; scientific verification retains its own source evidence.",
    report.summary,
    "## Experiments and issues",
    ...(report.experiments || []).map(
      (value: Record<string, any>) =>
        `### ${value.what_was_done}\n${value.result}\n${value.problems}\n${refs(value)}`,
    ),
    "## Findings",
    ...(report.findings || []).map(
      (value: Record<string, any>) =>
        `### ${value.title}\n${value.evidence_label} · ${value.severity}\n${value.detail}\n${refs(value)}`,
    ),
    "## Recommended next steps",
    ...(report.recommendations || []).map(
      (value: Record<string, any>) =>
        `### ${value.title}\n${value.priority} · ${value.kind}\n${value.action}\n${value.rationale}\n${refs(value)}${value.judgment ? `\nJudgment:\n${JSON.stringify(value.judgment, null, 2)}` : ""}`,
    ),
    "## Evidence and reading limits",
    ...(report.limitations || []),
    "## Literature and citations",
    ...(result.citations || []).map(
      (value) =>
        `### ${value.title}\nCitation: ${value.id}\n${Array.isArray(value.authors) ? value.authors.join(", ") : value.authors || ""} · ${value.year ?? ""}\nSource: ${value.source}\nRead scope: ${value.read_scope}\nDOI: ${value.doi || ""}\nURL: ${analysisReferenceUrl(value.url) || ""}\n${value.abstract || ""}`,
    ),
    "## Coverage, retrieval receipts and source artifacts",
    JSON.stringify(
      {
        coverage: report.coverage,
        literature_searches: result.literature_searches,
        artifacts: result.artifacts,
      },
      null,
      2,
    ),
  ].join("\n\n");
}
