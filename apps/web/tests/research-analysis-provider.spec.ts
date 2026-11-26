import { expect, test, type Route } from "@playwright/test";

test("provider switch requires review and preserves replay of a prior analysis", async ({
  page,
  request,
}) => {
  const agents = await (await request.get("/api/agents")).json();
  const researcher = agents.find(
    (agent: { role: string }) => agent.role === "Researcher",
  );
  expect(researcher).toBeTruthy();
  const agentPath = `/api/agents/${researcher.id}`;
  const projectReceipt = await request.post("/api/projects", {
    data: { name: "Analysis provider change fixture", mode: "manual" },
  });
  await expect(projectReceipt).toBeOK();
  const project = await projectReceipt.json();
  const providerIds: string[] = [];
  const analysisPath = `/api/projects/${project.id}/research/analysis`;
  const runsPath = `/api/projects/${project.id}/runs`;

  try {
    for (const name of ["First local provider", "Second local provider"]) {
      const created = await request.post("/api/providers", {
        data: {
          name,
          kind: "ollama",
          base_url: "http://127.0.0.1:9",
          model: "offline-fixture",
          allow_paid: false,
        },
      });
      await expect(created).toBeOK();
      providerIds.push((await created.json()).id);
    }
    const [first, second] = providerIds;
    const select = async (id: string) =>
      expect(
        await request.patch(agentPath, { data: { provider_id: id } }),
      ).toBeOK();
    await select(first);
    const baseline = await (
      await request.get(`${analysisPath}/context`)
    ).json();
    await page.goto(
      `/projects/${project.id}/workspace?analysis=1&analysis_scope=project`,
    );
    const panel = page.locator(".research-analysis-panel");
    const question = panel.getByLabel("Analysis question");
    const submit = panel.getByRole("button", { name: "Request AI analysis" });
    await expect(
      panel.getByText("First local provider · offline-fixture"),
    ).toBeVisible();
    await question.fill("Keep this saved question when the provider changes");
    await expect(submit).toBeEnabled();

    await select(second);
    const current = await (await request.get(`${analysisPath}/context`)).json();
    expect(current.snapshot).toEqual(baseline.snapshot);
    await page.evaluate(() =>
      window.dispatchEvent(new Event("forest-refresh")),
    );
    await expect(
      panel.getByText("Second local provider · offline-fixture"),
    ).toBeVisible();
    expect(await (await request.get(runsPath)).json()).toEqual([]);
    await expect(submit).toBeDisabled({ timeout: 2000 });
    await expect(panel.getByRole("status")).toContainText("Provider changed");

    await panel.getByRole("button", { name: "Use current context" }).click();
    await expect(question).toHaveValue(
      "Keep this saved question when the provider changes",
    );
    await expect(submit).toBeEnabled();
    const response = page.waitForResponse(
      (item) =>
        item.request().method() === "POST" &&
        new URL(item.url()).pathname === analysisPath,
    );
    await submit.click();
    const received = await response;
    expect(received.ok()).toBeTruthy();
    expect(received.request().postDataJSON().provider_id).toBe(second);
    const queued = await (await request.get(runsPath)).json();
    expect(queued).toHaveLength(1);
    expect(queued[0].kind).toBe("research_analysis");
    expect(queued[0].config.provider_id).toBe(second);
    expect(queued[0].status).toBe("queued");

    // Drop only the browser's receipt. The real API commits this request first.
    let savedRun = "";
    let savedPayload: unknown;
    const routePattern = `**${analysisPath}`;
    const loseReceipt = async (route: Route) => {
      if (route.request().method() !== "POST") return route.continue();
      savedPayload = route.request().postDataJSON();
      const serverReceipt = await route.fetch();
      expect(serverReceipt.ok()).toBeTruthy();
      savedRun = (await serverReceipt.json()).id;
      await route.abort("failed");
    };
    await page.route(routePattern, loseReceipt);
    await question.fill("Original question sent to the second provider");
    await submit.click();
    await expect(
      panel.getByText("This submission identity is retained", { exact: false }),
    ).toBeVisible();
    await expect.poll(() => savedRun).not.toBe("");
    expect(await (await request.get(runsPath)).json()).toHaveLength(2);
    await page.unroute(routePattern, loseReceipt);

    await question.fill("New edits after the lost receipt");
    await select(first);
    await page.evaluate(() =>
      window.dispatchEvent(new Event("forest-refresh")),
    );
    await expect(
      panel.getByText("First local provider · offline-fixture"),
    ).toBeVisible();
    await expect(
      panel.getByText(
        "Retry sends the original request to Second local provider.",
        { exact: false },
      ),
    ).toBeVisible();
    await expect(submit).toHaveCount(0);
    const replay = page.waitForResponse(
      (item) =>
        item.request().method() === "POST" &&
        new URL(item.url()).pathname === analysisPath,
    );
    await panel
      .getByRole("button", { name: "Retry with previous provider" })
      .click();
    const recovered = await replay;
    expect(recovered.ok()).toBeTruthy();
    expect(recovered.request().postDataJSON()).toEqual(savedPayload);
    expect((await recovered.json()).id).toBe(savedRun);
    await expect(
      panel
        .locator(".analysis-run-receipt")
        .getByText(savedRun, { exact: true }),
    ).toBeVisible();
    await expect(question).toHaveValue("New edits after the lost receipt");
    expect(await (await request.get(runsPath)).json()).toHaveLength(2);
  } finally {
    await request.patch(agentPath, {
      data: { provider_id: researcher.provider_id },
    });
    await request.delete(`/api/projects/${project.id}`);
    for (const id of providerIds)
      await request.post(`/api/providers/${id}/retire`);
  }
});
