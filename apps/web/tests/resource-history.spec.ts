import { expect, test } from "@playwright/test";

const cases = [
  ["experiments", "experiments"],
  ["library", "library"],
  ["ideas", "ideas"],
  ["theory", "theories"],
  ["figures", "figures"],
] as const;

for (const [pageName, resource] of cases) {
  test(`${pageName} reaches saved records beyond the first page`, async ({
    page,
  }) => {
    const project = {
      id: "records-probe",
      name: "Records fixture",
      budget: {},
      config: {},
    };
    const records = Array.from({ length: 101 }, (_, index) => ({
      id: `record-${index + 1}`,
      project_id: project.id,
      title: `Saved record ${index + 1}`,
      revision: 1,
      status: "ready",
      updated_at: "2026-01-01T00:00:00Z",
      data: {},
    }));
    const requestedOffsets: number[] = [];
    await page.route("**/api/**", (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === `/api/${resource}`) {
        const offset = Number(url.searchParams.get("offset") || "0");
        const limit = Number(url.searchParams.get("limit") || "100");
        requestedOffsets.push(offset);
        return route.fulfill({ json: records.slice(offset, offset + limit) });
      }
      const base = `/api/projects/${project.id}`;
      if (url.pathname === base) return route.fulfill({ json: project });
      if (
        url.pathname.endsWith("/runs") ||
        url.pathname.endsWith("/providers")
      ) {
        return route.fulfill({ json: [] });
      }
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
      return route.fulfill({ json: {} });
    });

    await page.goto(`/projects/${project.id}/${pageName}`);
    await expect(
      page.getByText("Saved record 1", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      page.getByText("Saved record 101", { exact: true }),
    ).toHaveCount(0);
    await page.getByRole("button", { name: "Older records" }).click();
    await expect(
      page.getByText("Saved record 101", { exact: true }).first(),
    ).toBeVisible();
    expect(requestedOffsets).toContain(100);
    await expect(
      page.getByRole("button", { name: "Older records" }),
    ).toBeDisabled();
    await page.getByRole("button", { name: "Newer records" }).click();
    await expect(
      page.getByText("Saved record 1", { exact: true }).first(),
    ).toBeVisible();
  });
}
