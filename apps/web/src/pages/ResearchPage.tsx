import { useState, useEffect } from "react";
import { Plus, ArrowUpRight, Pencil, Check, Lightbulb } from "lucide-react";
import { api, uid, parseJson } from "../api";
import type { Json, RecordItem } from "../api";
import {
  useUI,
  Button,
  IconButton,
  Badge,
  Field,
  Empty,
  ErrorBox,
  JsonView,
  PageHeading,
  Loading,
} from "../ui";
import {
  RecordEditor,
  RecordActions,
  RecordPagination,
  useProjectRecords,
} from "../shared/records";

export function ResearchPage({ resource }: { resource: string }) {
  const {
    data: records,
    id,
    error,
    reload,
    loading,
    pagination,
  } = useProjectRecords(resource);
  const { t, action } = useUI();
  const [editing, setEditing] = useState<RecordItem | null | false>(false);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="page">
      <PageHeading
        title={t("想法", "Ideas")}
        actions={
          <Button onClick={() => setEditing(null)}>
            <Plus size={15} />
            {t("记录想法", "Add idea")}
          </Button>
        }
      />
      <section className="surface idea-prompt">
        <div>
          <h3>{t("研究问题", "Research question")}</h3>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={t(
              "描述希望探索的问题…",
              "Describe the question to explore…",
            )}
          />
          <Button
            className="primary"
            busy={busy}
            disabled={!prompt.trim()}
            onClick={async () => {
              setBusy(true);
              await action(
                async () => {
                  await api("/research/ideas", "POST", {
                    project_id: id,
                    prompt,
                    count: 3,
                  });
                  setPrompt("");
                  await reload();
                },
                t("研究任务已排队", "Research queued"),
              );
              setBusy(false);
            }}
          >
            <Lightbulb size={15} />
            {t("生成候选方向", "Generate directions")}
          </Button>
        </div>
      </section>
      <ErrorBox error={error} retry={reload} />
      <RecordPagination pagination={pagination} />
      {loading ? (
        <Loading />
      ) : records.length ? (
        <div className="idea-grid">
          {records.map((r) => {
            const meaningfulRange =
              r.data.base_case?.probability_meaningful_improvement;
            const hasMeaningfulRange =
              Array.isArray(meaningfulRange) && meaningfulRange.length === 2;
            const probabilityRange = hasMeaningfulRange
              ? meaningfulRange
              : r.data.probability_range;
            return (
              <article key={r.id} className="surface idea-card">
                <div className="section-toolbar">
                  <Badge status={r.status} />
                </div>
                <h2>{r.title}</h2>
                {[
                  ["hypothesis", "可证伪命题", "Falsifiable hypothesis"],
                  ["mechanism", "作用机制", "Mechanism"],
                  ["nearest_work", "最近邻工作", "Nearest work"],
                  ["decisive_experiment", "区分实验", "Decisive experiment"],
                  ["best_estimate", "当前最佳判断", "Best estimate"],
                ].map(
                  ([key, zh, en]) =>
                    r.data[key] && (
                      <div className="idea-field" key={key}>
                        <span>{t(zh, en)}</span>
                        <p>
                          {typeof r.data[key] === "string"
                            ? r.data[key]
                            : JSON.stringify(r.data[key])}
                        </p>
                      </div>
                    ),
                )}
                {probabilityRange && (
                  <div className="estimate">
                    <span>
                      {t(
                        hasMeaningfulRange
                          ? "有意义改进的估计概率"
                          : "估计概率范围（结果未分类）",
                        hasMeaningfulRange
                          ? "Estimated probability of meaningful improvement"
                          : "Estimated probability range (outcome unspecified)",
                      )}
                    </span>
                    <strong>{String(probabilityRange)}</strong>
                  </div>
                )}
                {Array.isArray(r.data.supporting_source_titles) && (
                  <div className="idea-field">
                    <span>
                      {t(
                        "模型选出的相关来源（引用前请核实）",
                        "Model-selected supporting or motivating sources (verify before citing)",
                      )}
                    </span>
                    {r.data.supporting_source_titles.length ? (
                      <ul>
                        {r.data.supporting_source_titles.map(
                          (title: string, index: number) => (
                            <li key={`${title}:${index}`}>{title}</li>
                          ),
                        )}
                      </ul>
                    ) : (
                      <p>
                        {t(
                          "没有检索来源被选为此方向的直接依据。",
                          "No retrieved source was selected as direct support for this direction.",
                        )}
                      </p>
                    )}
                  </div>
                )}
                {Array.isArray(r.data.background_source_titles) &&
                  r.data.background_source_titles.length > 0 && (
                    <div className="idea-field">
                      <span>
                        {t(
                          "检索背景（不作为此方向的直接依据）",
                          "Retrieved background (not direct support for this direction)",
                        )}
                      </span>
                      <ul>
                        {r.data.background_source_titles.map(
                          (title: string, index: number) => (
                            <li key={`${title}:${index}`}>{title}</li>
                          ),
                        )}
                      </ul>
                    </div>
                  )}
                {!Array.isArray(r.data.supporting_source_titles) &&
                  Array.isArray(r.data.source_titles) &&
                  r.data.source_titles.length > 0 && (
                    <div className="idea-field">
                      <span>
                        {t(
                          "旧记录的检索来源池（未归属到此方向）",
                          "Retrieved source pool in this older record (not attributed to this direction)",
                        )}
                      </span>
                      <ul>
                        {r.data.source_titles.map(
                          (title: string, index: number) => (
                            <li key={`${title}:${index}`}>{title}</li>
                          ),
                        )}
                      </ul>
                    </div>
                  )}
                {Array.isArray(r.data.commands) && (
                  <ProposalCommands record={r} onApplied={reload} />
                )}
                <details>
                  <summary>{t("查看完整分析", "Full analysis")}</summary>
                  <JsonView value={r.data} />
                </details>
                <div className="record-footer">
                  <RecordActions
                    resource={resource}
                    item={r}
                    reload={reload}
                    onEdit={() => setEditing(r)}
                  />
                  <Button
                    className="primary"
                    disabled={Array.isArray(r.data.commands)}
                    onClick={() =>
                      action(
                        async () => {
                          await api(`/ideas/${r.id}/adopt`, "POST", {});
                          await reload();
                        },
                        t("已采用为研究节点", "Adopted as research nodes"),
                      )
                    }
                  >
                    {t("采用此路线", "Adopt direction")}
                    <ArrowUpRight size={14} />
                  </Button>
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <Empty title={t("暂无想法", "No ideas")} />
      )}{" "}
      {editing !== false && (
        <RecordEditor
          key={editing === null ? "new" : editing.id}
          resource={resource}
          item={editing || undefined}
          projectId={id!}
          preserveDraft
          initial={{
            hypothesis: "",
            mechanism: "",
            nearest_work: "",
            decisive_experiment: "",
            best_estimate: "",
            probability_range: "",
            confidence: "",
            against: "",
            resources: {},
          }}
          onClose={() => setEditing(false)}
          onSaved={reload}
        />
      )}
    </div>
  );
}

function ProposalCommands({
  record,
  onApplied,
}: {
  record: RecordItem;
  onApplied: () => Promise<void>;
}) {
  const { t, action } = useUI();
  const [selected, setSelected] = useState<number[]>([]);
  const [commands, setCommands] = useState<string[]>(
    record.data.commands.map((c: Json) => JSON.stringify(c, null, 2)),
  );
  const [edit, setEdit] = useState<number | null>(null);
  const [reviewedRevision, setReviewedRevision] = useState<number | null>(null);
  const [rejectionReason, setRejectionReason] = useState("");
  useEffect(() => {
    let active = true;
    api(`/projects/${record.project_id}/graph`).then((graph) => {
      if (active) setReviewedRevision(graph.revision);
    });
    return () => {
      active = false;
    };
  }, [record.id, record.project_id]);
  return (
    <div className="proposal-commands">
      <strong>{t("路线建议", "Path suggestions")}</strong>
      {commands.map((text, index) => {
        let command: Json;
        try {
          command = JSON.parse(text);
        } catch {
          command = { operation: "Invalid JSON" };
        }
        const accepted = (record.data.accepted_indices || []).includes(index);
        const appliedCommand = record.data.applied_commands?.[index];
        const displayedCommand = accepted ? appliedCommand || command : command;
        const originalCommand = record.data.commands[index];
        const hasAppliedEdit =
          appliedCommand &&
          JSON.stringify(appliedCommand) !== JSON.stringify(originalCommand);
        return (
          <div className="proposal-command" key={index}>
            <input
              type="checkbox"
              aria-label={`Select command ${index + 1}`}
              checked={selected.includes(index)}
              disabled={accepted}
              onChange={(e) =>
                setSelected((s) =>
                  e.target.checked
                    ? [...s, index]
                    : s.filter((i) => i !== index),
                )
              }
            />
            <div>
              <code>{displayedCommand.operation}</code>
              {accepted && (
                <Badge status="adopted">{t("已采用", "Applied")}</Badge>
              )}
              <p>
                {displayedCommand.params?.title ||
                  displayedCommand.params?.name ||
                  displayedCommand.targets?.join(", ")}
              </p>
              {hasAppliedEdit && (
                <details>
                  <summary>{t("原始建议", "Original suggestion")}</summary>
                  <pre>{JSON.stringify(originalCommand, null, 2)}</pre>
                </details>
              )}
              {edit === index && (
                <textarea
                  rows={8}
                  className="code-input"
                  value={text}
                  onChange={(e) =>
                    setCommands((s) =>
                      s.map((x, i) => (i === index ? e.target.value : x)),
                    )
                  }
                />
              )}
            </div>
            <IconButton
              label={t("修改建议", "Edit suggestion")}
              disabled={accepted}
              onClick={() => setEdit(edit === index ? null : index)}
            >
              <Pencil size={13} />
            </IconButton>
          </div>
        );
      })}
      <Button
        className="primary"
        disabled={!selected.length}
        onClick={() =>
          action(
            async () => {
              await api(`/research/proposals/${record.id}/apply`, "POST", {
                request_id: uid(),
                expected_revision: reviewedRevision,
                indices: selected,
                commands: commands.map(parseJson),
              });
              setSelected([]);
              await onApplied();
            },
            t("已采用选中路线变更", "Selected path changes applied"),
          )
        }
      >
        <Check size={14} />
        {t("采用选中建议", "Apply selected suggestions")} ({selected.length})
      </Button>
      <Field label={t("拒绝理由", "Proposal rejection reason")}>
        <textarea
          value={rejectionReason}
          onChange={(event) => setRejectionReason(event.target.value)}
        />
      </Field>
      <Button
        disabled={!rejectionReason.trim() || reviewedRevision === null}
        onClick={() =>
          action(async () => {
            await api(`/research/proposals/${record.id}/reject`, "POST", {
              expected_revision: reviewedRevision,
              reason: rejectionReason,
            });
            await onApplied();
          })
        }
      >
        {t("拒绝建议", "Reject proposal")}
      </Button>
    </div>
  );
}
