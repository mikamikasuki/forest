import { expect, test } from "@playwright/test";

test("Workspace can open a run beyond the first 500 records", async ({
  page,
}) => {
  const project = {
    id: "run-history-probe",
    name: "Run history",
    budget: {},
    config: {},
  };
  const runs = Array.from({ length: 501 }, (_, index) => ({
    id: `run-${index + 1}`,
    project_id: project.id,
    node_id: null,
    branch_id: null,
    kind: `History entry ${index + 1}`,
    status: "completed",
    created_at: "2026-01-01T00:00:00Z",
    config: {},
    metrics: {},
  }));
  const requests: number[] = [];
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const base = `/api/projects/${project.id}`;
    if (url.pathname === `${base}/runs`) {
      const limit = Number(url.searchParams.get("limit") || "100");
      const offset = Number(url.searchParams.get("offset") || "0");
      if (limit === 500) requests.push(offset);
      return route.fulfill({ json: runs.slice(offset, offset + limit) });
    }
    if (url.pathname === base) return route.fulfill({ json: project });
    if (url.pathname === `${base}/graph`) {
      return route.fulfill({
        json: {
          project_id: project.id,
          revision: 1,
          branches: [],
          nodes: [],
          edges: [],
        },
      });
    }
    if (url.pathname === "/api/runs/run-501")
      return route.fulfill({ json: runs[500] });
    if (url.pathname === "/api/runs/run-501/output") {
      return route.fulfill({ json: { stdout: "", stderr: "", offset: 0 } });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto(`/projects/${project.id}/workspace`);
  const dock = page.locator(".run-dock");
  await dock.getByRole("button", { name: /^Runs/ }).click();
  const history = dock.locator(".run-list");
  await expect(
    history.getByRole("button", { name: /^History entry 1\b/ }),
  ).toBeVisible();
  expect(requests).toContain(0);
  await expect(
    history.getByRole("button", { name: /^History entry 501\b/ }),
  ).toHaveCount(0);

  await dock.getByRole("button", { name: "Older runs" }).click();
  const older = history.getByRole("button", { name: /^History entry 501\b/ });
  await expect(older).toBeVisible();
  expect(requests).toContain(500);
  await older.click();
  await expect(page).toHaveURL(/\brun=run-501\b/);

  await dock.getByRole("button", { name: "Newer runs" }).click();
  await expect(
    history.getByRole("button", { name: /^History entry 1\b/ }),
  ).toBeVisible();
});
