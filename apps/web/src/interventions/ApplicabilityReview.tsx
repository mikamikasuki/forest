import { useState } from "react";
import { api, ApiError, uid } from "../api";
import type { Project } from "../api";
import type { components } from "../../../../packages/contracts/api.generated";
import { Badge, Button, ErrorBox, Field, Modal, useLoad, useUI } from "../ui";

type Applicability = components["schemas"]["ApplicabilityView"];
type EvidenceReview = {
  goal: string;
  revision: number;
  executionGoals: Applicability["execution_goals"];
  requestId: string;
  stale: boolean;
};

export function ApplicabilityReview({
  projectId,
  runId,
  completed,
}: {
  projectId: string;
  runId: string;
  completed: boolean;
}) {
  const { t, action } = useUI();
  const path = `/runs/${runId}/applicability`;
  const value = useLoad<Applicability | null>(path, null);
  const [review, setReview] = useState<EvidenceReview | null>(null);
  const [opening, setOpening] = useState(false);
  const [reason, setReason] = useState("");
  const [choice, setChoice] = useState<"reuse" | "exclude">("reuse");
  return (
    <section className="run-evidence-attempts">
      <strong>{t("当前目标适用性", "Applicability to current goal")}</strong>
      <ErrorBox error={value.error} />
      {value.data && (
        <>
          <Badge status={value.data.status} />
          <p>{value.data.reason}</p>
          <p className="muted">
            {t(
              "适用性与计算验证独立；目标改变不会删除原始计算或验证回执。",
              "Applicability and computational verification are separate. Goal changes preserve original computations and verification receipts.",
            )}
          </p>
          {completed && (
            <Button
              busy={opening}
              onClick={() =>
                action(async () => {
                  setOpening(true);
                  try {
                    const [applicability, project] = await Promise.all([
                      api<Applicability>(path),
                      api<Project>(`/projects/${projectId}`),
                    ]);
                    value.setData(applicability);
                    // The project response binds the displayed goal to its revision.
                    setReview({
                      goal: project.goal,
                      revision: project.revision,
                      executionGoals: applicability.execution_goals,
                      requestId: uid(),
                      stale: false,
                    });
                    setReason("");
                    setChoice("reuse");
                  } finally {
                    setOpening(false);
                  }
                })
              }
            >
              {t("审阅证据用途", "Review evidence use")}
            </Button>
          )}
        </>
      )}
      {review && (
        <Modal
          title={t(
            "审阅证据与当前目标",
            "Review evidence against current goal",
          )}
          onClose={() => setReview(null)}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (review.stale) return;
              void action(async () => {
                try {
                  await api(`${path}/decisions`, "POST", {
                    request_id: review.requestId,
                    expected_revision: review.revision,
                    choice,
                    reason,
                  });
                } catch (error) {
                  if (
                    error instanceof ApiError &&
                    error.code === "REVISION_CONFLICT"
                  ) {
                    setReview((current) =>
                      current?.requestId === review.requestId
                        ? { ...current, stale: true }
                        : current,
                    );
                    void value.reload();
                    return;
                  }
                  throw error;
                }
                setReview((current) =>
                  current?.requestId === review.requestId ? null : current,
                );
                await value.reload();
              });
            }}
          >
            <Field label={t("当前目标", "Current goal")}>
              <p>{review.goal}</p>
            </Field>
            <Field
              label={t("执行时采用的目标", "Goals recorded during execution")}
            >
              <pre>{JSON.stringify(review.executionGoals, null, 2)}</pre>
            </Field>
            <Field label={t("证据用途", "Evidence use")}>
              <select
                value={choice}
                onChange={(event) =>
                  setChoice(event.target.value as typeof choice)
                }
              >
                <option value="reuse">
                  {t("允许用于当前目标", "Allow use for current goal")}
                </option>
                <option value="exclude">
                  {t(
                    "保留历史，排除当前用途",
                    "Preserve history and exclude current use",
                  )}
                </option>
              </select>
            </Field>
            <Field label={t("科学理由", "Scientific reason")}>
              <textarea
                required
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </Field>
            {review.stale && (
              <ErrorBox
                error={t(
                  "项目已更改。请关闭此审阅，然后重新打开以确认当前目标。",
                  "The project changed. Close and reopen this review to confirm the current goal.",
                )}
              />
            )}
            <Button
              type="submit"
              className="primary"
              disabled={review.stale || !reason.trim()}
            >
              {t("保存证据用途决定", "Save evidence-use decision")}
            </Button>
          </form>
        </Modal>
      )}
    </section>
  );
}
