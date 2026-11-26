import { expect, test } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";
import { resolve } from "node:path";

const workload = resolve(process.cwd(), "../../scripts/test_worker_case.py");
const runtimePython = process.env.FOREST_WORKER_PYTHON || "python3";
const terminal = ["completed", "failed", "cancelled", "interrupted", "skipped"];

async function createProject(request: APIRequestContext, name: string) {
  const response = await request.post("/api/projects", {
    data: {
      name: `${name} ${Date.now()}`,
      goal: "Validate isolated actual process controls without a model provider.",
      mode: "manual",
      budget: { seconds: 120, allow_paid: false },
      config: { publication_profile: { id: "operational" } },
    },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).id as string;
}
async function enqueue(
  request: APIRequestContext,
  projectId: string,
  title: string,
  seconds: number,
  checkpoint = false,
) {
  const graph = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  const response = await request.post(
    `/api/projects/${projectId}/graph/commands`,
    {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: graph.revision,
        operation: "add_node",
        params: {
          type: "implementation",
          title,
          position: { x: 120 + graph.nodes.length * 300, y: 120 },
          config: {
            kind: "command",
            timeout: seconds + 20,
            command: [
              runtimePython,
              workload,
              "--seconds",
              String(seconds),
              ...(checkpoint ? ["--checkpoint", "after_model"] : []),
            ],
          },
        },
      },
    },
  );
  expect(response.ok(), await response.text()).toBeTruthy();
  const updated = await response.json();
  const node = updated.graph.nodes.find(
    (item: { title: string }) => item.title === title,
  );
  const submitted = await request.post(`/api/nodes/${node.id}/run`, {
    data: { request_id: crypto.randomUUID(), scope: "single" },
  });
  expect(submitted.ok(), await submitted.text()).toBeTruthy();
  return submitted.json();
}
async function readRun(request: APIRequestContext, id: string) {
  const response = await request.get(`/api/runs/${id}`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}
async function cleanup(
  request: APIRequestContext,
  projectId: string,
  runIds: string[],
) {
  for (const id of runIds) {
    const run = await readRun(request, id);
    if (!terminal.includes(run.status)) {
      expect(
        (await request.post(`/api/runs/${id}/cancel`, { data: {} })).ok(),
      ).toBeTruthy();
      await expect
        .poll(async () => (await readRun(request, id)).status)
        .toBe("cancelled");
    }
  }
  expect(
    (await request.delete(`/api/projects/${projectId}`)).ok(),
  ).toBeTruthy();
}

test("evidence review saves only against the displayed goal", async ({
  page,
  request,
}) => {
  const projectId = await createProject(
    request,
    "Evidence review goal binding",
  );
  const projectPath = `/api/projects/${projectId}`;
  const runIds: string[] = [];
  try {
    const run = await enqueue(
      request,
      projectId,
      "Provider-free evidence",
      0.5,
    );
    runIds.push(run.id);
    await expect
      .poll(async () => (await readRun(request, run.id)).status)
      .toBe("completed");
    const executionGoal = (await readRun(request, run.id)).config.project_goal;
    const applicabilityPath = `/api/runs/${run.id}/applicability`;
    const decisionPath = `${applicabilityPath}/decisions`;
    const review = page.getByRole("button", { name: "Review evidence use" });
    const dialog = page.getByRole("dialog", {
      name: "Review evidence against current goal",
    });
    const save = dialog.getByRole("button", {
      name: "Save evidence-use decision",
    });
    const reason = dialog.locator("textarea");
    const currentGoal = dialog.locator("p").first();
    const goalAfterPanel = "Goal revised while the evidence panel is open";
    const goalAfterReview = "Goal revised while the evidence review is open";

    const changeGoal = async (goal: string) => {
      const project = await (await request.get(projectPath)).json();
      const edited = await request.patch(projectPath, {
        data: { goal, expected_revision: project.revision },
      });
      expect(edited.ok(), await edited.text()).toBeTruthy();
      return edited.json();
    };
    const saveReview = async () => {
      const response = page.waitForResponse(
        (result) =>
          result.request().method() === "POST" &&
          result.url().endsWith(decisionPath),
      );
      await save.click();
      return response;
    };

    await page.goto(`/projects/${projectId}/runs?run=${run.id}`);
    const original = page.waitForResponse((response) =>
      response.url().endsWith(applicabilityPath),
    );
    await page.getByText("Evidence and recovery", { exact: true }).click();
    expect((await (await original).json()).current_goal).toBe(executionGoal);
    const revised = await changeGoal(goalAfterPanel);

    await review.click();
    await expect(currentGoal).toHaveText(goalAfterPanel);
    await expect(dialog.locator("pre")).toContainText(executionGoal);
    await reason.fill("The evidence applies to the revised question.");
    const saved = await saveReview();
    expect(saved.ok(), await saved.text()).toBeTruthy();
    expect(saved.request().postDataJSON().expected_revision).toBe(
      revised.revision,
    );
    const receipt = await saved.json();
    expect(
      (await (await request.get(`/api/interventions/${receipt.id}`)).json())
        .intent,
    ).toMatchObject({
      goal: goalAfterPanel,
      run_id: run.id,
      choice: "reuse",
    });
    await expect(dialog).toBeHidden();

    await review.click();
    await expect(currentGoal).toHaveText(goalAfterPanel);
    await reason.fill("This draft belongs to the goal shown in this review.");
    const refreshed = page.waitForResponse(
      async (response) =>
        response.request().method() === "GET" &&
        response.url().endsWith(projectPath) &&
        (await response.json()).goal === goalAfterReview,
    );
    await changeGoal(goalAfterReview);
    await refreshed;
    await expect(currentGoal).toHaveText(goalAfterPanel);
    await expect(reason).toHaveValue(
      "This draft belongs to the goal shown in this review.",
    );
    const stale = await saveReview();
    expect(stale.status()).toBe(409);
    expect((await stale.json()).detail.code).toBe("REVISION_CONFLICT");
    await expect(save).toBeDisabled();
    await expect(reason).toHaveValue(
      "This draft belongs to the goal shown in this review.",
    );
    const interventions = await (
      await request.get(`${projectPath}/interventions`)
    ).json();
    expect(
      interventions
        .filter(
          (item: { kind: string }) => item.kind === "evidence_applicability",
        )
        .map((item: { id: string }) => item.id),
    ).toEqual([receipt.id]);

    await dialog.getByRole("button", { name: "Close" }).click();
    await review.click();
    await expect(currentGoal).toHaveText(goalAfterReview);
    await expect(reason).toHaveValue("");
  } finally {
    await cleanup(request, projectId, runIds);
  }
});

test("actual stopped debug checkpoint saves an override separately from same-run Resume", async ({
  page,
  request,
}) => {
  test.setTimeout(45000);
  const projectId = await createProject(
    request,
    "Actual browser debug checkpoint",
  );
  const runIds: string[] = [];
  try {
    const run = await enqueue(
      request,
      projectId,
      "Provider-free debug callback",
      0.5,
      true,
    );
    runIds.push(run.id);
    await expect
      .poll(
        async () => {
          const saved = await readRun(request, run.id);
          return (
            saved.status === "waiting_input" &&
            saved.resource?.checkpoint?.stage
          );
        },
        { timeout: 30000 },
      )
      .toBe("after_model");
    const stopped = await readRun(request, run.id);
    expect(stopped.resource.checkpoint.payload).toEqual({
      text: "original test callback payload",
    });
    await page.goto(`/projects/${projectId}/runs?run=${run.id}`);
    await page
      .getByRole("button", { name: "Run advanced controls", exact: true })
      .click();
    const dialog = page.getByRole("dialog", {
      name: "Run controls and diagnostics",
    });
    await expect(
      dialog.getByText("Current stopped debug checkpoint", { exact: true }),
    ).toBeVisible();
    await expect(dialog).toContainText("after_model");
    await expect(dialog).toContainText("original test callback payload");
    const editor = dialog.getByLabel("Debug checkpoint override (JSON)");
    await expect
      .poll(async () => JSON.parse(await editor.inputValue()))
      .toEqual(stopped.resource.checkpoint.payload);
    const override = {
      text: "actual browser edited callback payload",
      unknown_callback_field: { retained: true },
    };
    await editor.fill(JSON.stringify(override));
    const resumeRequests: string[] = [];
    page.on("request", (value) => {
      if (
        value.method() === "POST" &&
        new URL(value.url()).pathname === `/api/runs/${run.id}/resume`
      )
        resumeRequests.push(value.url());
    });
    const patch = page.waitForResponse(
      (response) =>
        response.request().method() === "PATCH" &&
        new URL(response.url()).pathname === `/api/runs/${run.id}/checkpoint`,
    );
    await dialog
      .getByRole("button", {
        name: "Save checkpoint without resuming",
        exact: true,
      })
      .click();
    expect((await patch).ok()).toBeTruthy();
    const savedOverride = await readRun(request, run.id);
    expect(savedOverride.status).toBe("waiting_input");
    expect(savedOverride.resource.checkpoint.payload).toEqual(
      stopped.resource.checkpoint.payload,
    );
    expect(savedOverride.resource.checkpoint.override).toEqual(override);
    expect(resumeRequests).toEqual([]);
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await page
      .locator(".run-output-toolbar")
      .getByRole("button", { name: "resume", exact: true })
      .click();
    await expect
      .poll(async () => (await readRun(request, run.id)).status, {
        timeout: 20000,
      })
      .toBe("completed");
    const completed = await readRun(request, run.id);
    expect(completed.id).toBe(stopped.id);
    expect(completed.metrics.checkpoint_value).toEqual(override);
    expect(completed.metrics.ticks).toBeGreaterThan(0);
    const records = await (
      await request.get(`/api/projects/${projectId}/runs`)
    ).json();
    expect(records.map((record: { id: string }) => record.id)).toEqual([
      run.id,
    ]);
    const metrics = page.locator(".run-recorded-metrics");
    await metrics.getByText("Recorded metrics", { exact: true }).click();
    await expect(metrics).toContainText(run.id);
    await expect(metrics).toContainText(override.text);
    await expect(metrics).toContainText("unknown_callback_field");
  } finally {
    await cleanup(request, projectId, runIds);
  }
});

test("actual pause and Resume keep the run identity, and exact cancel leaves a neighboring run intact", async ({
  page,
  request,
}) => {
  test.setTimeout(45000);
  const projectId = await createProject(
    request,
    "Actual browser process controls",
  );
  const runIds: string[] = [];
  try {
    const target = await enqueue(
      request,
      projectId,
      "Controlled actual process",
      30,
    );
    runIds.push(target.id);
    const neighbor = await enqueue(
      request,
      projectId,
      "Unaffected actual neighbor",
      6,
    );
    runIds.push(neighbor.id);
    for (const id of runIds)
      await expect
        .poll(async () => (await readRun(request, id)).status, {
          timeout: 30000,
        })
        .toBe("running");
    const running = await readRun(request, target.id);
    await page.goto(`/projects/${projectId}/runs?run=${target.id}`);
    const toolbar = page.locator(".run-output-toolbar");
    await toolbar.getByRole("button", { name: "pause", exact: true }).click();
    await expect
      .poll(async () => (await readRun(request, target.id)).status)
      .toBe("paused");
    await expect(
      toolbar.getByRole("button", { name: "resume", exact: true }),
    ).toBeEnabled();
    await toolbar.getByRole("button", { name: "resume", exact: true }).click();
    await expect
      .poll(async () => (await readRun(request, target.id)).status)
      .toBe("running");
    const resumed = await readRun(request, target.id);
    expect(resumed.id).toBe(running.id);
    expect(resumed.pid).toBe(running.pid);
    expect(resumed.config.execution_attempt.id).toBe(
      running.config.execution_attempt.id,
    );
    await toolbar.getByRole("button", { name: "cancel", exact: true }).click();
    await expect
      .poll(async () => (await readRun(request, target.id)).status)
      .toBe("cancelled");
    expect(["running", "completed"]).toContain(
      (await readRun(request, neighbor.id)).status,
    );
    await expect
      .poll(async () => (await readRun(request, neighbor.id)).status, {
        timeout: 20000,
      })
      .toBe("completed");
    expect((await readRun(request, neighbor.id)).metrics.ticks).toBeGreaterThan(
      0,
    );
    const records = await (
      await request.get(`/api/projects/${projectId}/runs`)
    ).json();
    expect(records.map((record: { id: string }) => record.id).sort()).toEqual(
      runIds.slice().sort(),
    );
    const neighborOutput = await (
      await request.get(`/api/runs/${neighbor.id}/output`)
    ).json();
    expect(neighborOutput.text).toContain("actual_process_finished");
  } finally {
    await cleanup(request, projectId, runIds);
  }
});
