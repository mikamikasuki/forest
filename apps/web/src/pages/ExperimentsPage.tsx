import { useState, useEffect, useRef } from "react";
import { Plus, Play, FlaskConical, Save, GitCompare } from "lucide-react";
import { api, uid, parseJson, formatDate } from "../api";
import type { Graph, Json, RecordItem, Run } from "../api";
import { ExecutionSettings } from "../ExecutionSettings";
import {
  useUI,
  useLoad,
  Button,
  Badge,
  Modal,
  Field,
  Empty,
  ErrorBox,
  JsonView,
  PageHeading,
} from "../ui";
import { CodeEditor } from "../editors";
import { RunPanel } from "../runs/RunPanel";
import {
  RecordActions,
  RecordPagination,
  useProjectRecords,
} from "../shared/records";
import { compactMetrics } from "../shared/metrics";

export function ExperimentsPage() {
  const {
    data: experiments,
    id,
    error,
    reload,
    pagination,
  } = useProjectRecords("experiments");
  const { data: runs, reload: reloadRuns } = useLoad<Run[]>(
    `/projects/${id}/runs`,
    [],
  );
  const graph = useLoad<Graph | null>(`/projects/${id}/graph`, null);
  const { t, action } = useUI();
  const [editing, setEditing] = useState<RecordItem | null | false>(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [comparison, setComparison] = useState<Json | null>(null);
  const [launchingExperimentIds, setLaunchingExperimentIds] = useState(
    () => new Set<string>(),
  );
  const launchingExperimentIdsRef = useRef(new Set<string>());
  useEffect(() => {
    const f = () => void reloadRuns();
    window.addEventListener("forest-refresh", f);
    return () => window.removeEventListener("forest-refresh", f);
  }, [reloadRuns]);
  return (
    <div className="page">
      <PageHeading
        title={t("实验", "Experiments")}
        actions={
          <Button className="primary" onClick={() => setEditing(null)}>
            <Plus size={15} />
            {t("新建实验", "New experiment")}
          </Button>
        }
      />
      <ErrorBox error={error} retry={reload} />
      <RecordPagination pagination={pagination} />
      {experiments.length ? (
        <div className="experiment-grid">
          {experiments.map((e) => (
            <section key={e.id} className="surface experiment-card">
              <div className="section-toolbar">
                <span className="experiment-icon">
                  <FlaskConical size={19} />
                </span>
                <Badge status={e.status} />
              </div>
              <h2>{e.title}</h2>
              <p>
                {e.data.duty ||
                  t("尚未填写论证职责", "No argumentative duty set")}
              </p>
              <div className="experiment-meta">
                <span>
                  Seeds<strong>{(e.data.seeds || []).join(", ") || "—"}</strong>
                </span>
                <span>
                  {t("预算", "Budget")}
                  <strong>
                    {e.data.budget?.seconds ? `${e.data.budget.seconds}s` : "—"}
                  </strong>
                </span>
              </div>
              <code className="command-preview">
                {e.data.command ||
                  e.data.entrypoint ||
                  (e.data.example === "class_weight_calibration"
                    ? e.data.example
                    : t(
                        "请配置执行命令；保存的代码仅供参考。",
                        "Configure an execution command; saved code is reference only.",
                      ))}
              </code>
              <div className="record-footer">
                <RecordActions
                  resource="experiments"
                  item={e}
                  reload={reload}
                  onEdit={() => setEditing(e)}
                />
                <Button
                  className="primary"
                  busy={launchingExperimentIds.has(e.id)}
                  disabled={launchingExperimentIds.has(e.id)}
                  onClick={() => {
                    if (launchingExperimentIdsRef.current.has(e.id)) return;
                    launchingExperimentIdsRef.current.add(e.id);
                    setLaunchingExperimentIds((current) =>
                      new Set(current).add(e.id),
                    );
                    void action(
                      async () => {
                        try {
                          await api(`/experiments/${e.id}/launch`, "POST", {
                            request_id: uid(),
                          });
                          await reloadRuns();
                        } finally {
                          launchingExperimentIdsRef.current.delete(e.id);
                          setLaunchingExperimentIds((current) => {
                            if (!current.has(e.id)) return current;
                            const next = new Set(current);
                            next.delete(e.id);
                            return next;
                          });
                        }
                      },
                      t(
                        "实验已加入真实执行队列",
                        "Experiment queued for execution",
                      ),
                    );
                  }}
                >
                  <Play size={13} />
                  {t("启动实验", "Launch")}
                </Button>
              </div>
            </section>
          ))}
        </div>
      ) : (
        <Empty
          icon={<FlaskConical size={28} />}
          title={t("暂无实验", "No experiments")}
          action={
            <Button onClick={() => setEditing(null)}>
              <Plus size={15} />
              {t("配置实验", "Configure experiment")}
            </Button>
          }
        />
      )}
      <section className="surface">
        <div className="section-toolbar">
          <h2>
            {t("任务与结果", "Tasks & results")}
            <span className="count">{runs.length}</span>
          </h2>
          <Button
            disabled={selected.length < 2}
            onClick={() =>
              action(async () =>
                setComparison(
                  await api("/experiments/compare", "POST", {
                    run_ids: selected,
                  }),
                ),
              )
            }
          >
            <GitCompare size={15} />
            {t("比较选中运行", "Compare runs")} ({selected.length})
          </Button>
        </div>
        {runs.length > 0 && (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th />
                  <th>{t("运行", "Run")}</th>
                  <th>{t("状态", "Status")}</th>
                  <th>{t("执行时间", "Started")}</th>
                  <th>{t("实际指标", "Measured metrics")}</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selected.includes(r.id)}
                        onChange={(e) =>
                          setSelected((s) =>
                            e.target.checked
                              ? [...s, r.id]
                              : s.filter((x) => x !== r.id),
                          )
                        }
                        aria-label={`Select ${r.id}`}
                      />
                    </td>
                    <td>
                      <strong>{r.kind}</strong>
                      <small>{r.id.slice(0, 8)}</small>
                    </td>
                    <td>
                      <Badge status={r.status} />
                    </td>
                    <td>{formatDate(r.started_at)}</td>
                    <td>
                      <code>{compactMetrics(r.metrics || {})}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="experiment-runs">
          <RunPanel
            runs={runs}
            reload={reloadRuns}
            nodes={graph.data?.nodes || []}
            branches={graph.data?.branches || []}
          />
        </div>
      </section>
      {comparison && (
        <Modal
          wide
          title={t("运行结果比较", "Run comparison")}
          onClose={() => setComparison(null)}
        >
          <JsonView value={comparison} />
        </Modal>
      )}
      {editing !== false && (
        <ExperimentForm
          key={editing === null ? "new" : editing.id}
          projectId={id!}
          item={editing || undefined}
          onClose={() => setEditing(false)}
          onSaved={reload}
        />
      )}
    </div>
  );
}

function ExperimentForm({
  projectId,
  onClose,
  onSaved,
  item,
}: {
  projectId: string;
  onClose: () => void;
  onSaved: () => void;
  item?: RecordItem;
}) {
  const { t, action } = useUI();
  const draftKey = `forest:experiment-draft:${projectId}:${item?.id || "new"}`;
  const [savedDraft] = useState(() => {
    if (typeof window === "undefined") return null;
    try {
      return JSON.parse(sessionStorage.getItem(draftKey) || "null");
    } catch {
      return null;
    }
  });
  const [title, setTitle] = useState(savedDraft?.title ?? item?.title ?? "");
  const [command, setCommand] = useState(
    savedDraft?.command ?? item?.data.command ?? "",
  );
  const [code, setCode] = useState(savedDraft?.code ?? item?.data.code ?? "");
  const [dataset, setDataset] = useState(
    savedDraft?.dataset ?? item?.data.dataset ?? "",
  );
  const [seeds, setSeeds] = useState<string>(
    savedDraft?.seeds ?? (item?.data.seeds || [0, 1, 2]).join(","),
  );
  const [params, setParams] = useState(
    savedDraft?.params ?? JSON.stringify(item?.data.parameters || {}, null, 2),
  );
  const [seconds, setSeconds] = useState(
    String(
      savedDraft?.seconds ??
        item?.data.timeout ??
        item?.data.budget?.seconds ??
        "",
    ),
  );
  const [duty, setDuty] = useState(
    savedDraft?.duty ?? item?.data.duty ?? "effectiveness",
  );
  const [executionConfig, setExecutionConfig] = useState<Json>(
    savedDraft?.executionConfig ?? item?.data ?? {},
  );
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      sessionStorage.setItem(
        draftKey,
        JSON.stringify({
          title,
          command,
          code,
          dataset,
          seeds,
          params,
          seconds,
          duty,
          executionConfig,
        }),
      );
    } catch {
      // Keep editing available when browser storage is disabled or full.
    }
  }, [
    command,
    code,
    dataset,
    draftKey,
    duty,
    executionConfig,
    params,
    seconds,
    seeds,
    title,
  ]);
  return (
    <Modal
      wide
      title={t("实验配置", "Experiment configuration")}
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          const parsedSeeds = seeds
            .split(",")
            .map((seed) => seed.trim())
            .filter(Boolean)
            .map(Number);
          const r = await action(() =>
            api(
              item ? `/experiments/${item.id}` : "/experiments",
              item ? "PATCH" : "POST",
              {
                project_id: projectId,
                title,
                data: {
                  ...item?.data,
                  ...executionConfig,
                  command,
                  code,
                  dataset,
                  seeds: parsedSeeds,
                  parameters: parseJson(params),
                  budget: seconds.trim() ? { seconds: Number(seconds) } : {},
                  timeout: seconds.trim() ? Number(seconds) : null,
                  duty,
                },
                ...(item ? { expected_revision: item.revision } : {}),
              },
            ),
          );
          setBusy(false);
          if (r) {
            try {
              sessionStorage.removeItem(draftKey);
            } catch {
              // Saving still succeeds if browser storage is unavailable.
            }
            onSaved();
            onClose();
          }
        }}
      >
        <div className="form-row">
          <Field label={t("实验名称", "Experiment name")}>
            <input
              required
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </Field>
          <Field label={t("论证职责", "Argumentative duty")}>
            <select value={duty} onChange={(e) => setDuty(e.target.value)}>
              <option value="effectiveness">
                {t("证明有效性", "Effectiveness")}
              </option>
              <option value="mechanism">
                {t("验证关键机制", "Mechanism")}
              </option>
              <option value="scenario">
                {t("验证场景价值", "Scenario value")}
              </option>
              <option value="alternative">
                {t("排除替代解释", "Alternative explanations")}
              </option>
            </select>
          </Field>
        </div>
        <Field label={t("数据路径或来源", "Dataset path or source")}>
          <input value={dataset} onChange={(e) => setDataset(e.target.value)} />
        </Field>
        <ExecutionSettings
          config={executionConfig}
          onChange={setExecutionConfig}
        />
        <Field label={t("实际执行命令", "Execution command")}>
          <input
            className="code-input"
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            placeholder="python experiment.py"
          />
        </Field>
        <Field
          label={t(
            "Python 参考代码（可选，不会自动执行）",
            "Python reference code (optional, not executed automatically)",
          )}
        >
          <div className="bounded-editor">
            <CodeEditor value={code} onChange={setCode} />
          </div>
        </Field>
        <div className="form-row">
          <Field
            label={t("随机种子（逗号分隔）", "Random seeds (comma separated)")}
          >
            <input value={seeds} onChange={(e) => setSeeds(e.target.value)} />
          </Field>
          <Field label="Time budget (seconds, optional)">
            <input
              type="number"
              min="1"
              placeholder="No time limit"
              value={seconds}
              onChange={(e) => setSeconds(e.target.value)}
            />
          </Field>
        </div>
        <Field
          label={t("超参数 / 扫描配置", "Parameters / sweep configuration")}
        >
          <textarea
            rows={4}
            className="code-input"
            value={params}
            onChange={(e) => setParams(e.target.value)}
          />
        </Field>
        <div className="modal-actions">
          <Button type="button" onClick={onClose}>
            {t("取消", "Cancel")}
          </Button>
          <Button className="primary" type="submit" busy={busy}>
            <Save size={14} />
            {t("保存实验配置", "Save experiment")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
