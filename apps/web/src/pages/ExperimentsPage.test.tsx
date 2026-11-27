import { expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { UIContext } from "../ui";
import { ExperimentsPage } from "./ExperimentsPage";

vi.mock("../shared/records", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../shared/records")>()),
  useProjectRecords: () => ({
    id: "project",
    error: "",
    reload: vi.fn(),
    data: [
      {
        id: "source",
        title: "Saved source",
        data: { code: "print('reference')" },
      },
      {
        id: "command",
        title: "Command",
        data: { command: "python experiment.py", seeds: [11, 22, 33] },
      },
      {
        id: "entrypoint",
        title: "Entrypoint",
        data: { entrypoint: "experiment:run" },
      },
      {
        id: "example",
        title: "Example",
        data: { example: "class_weight_calibration", seeds: [1, 2, 3] },
      },
    ],
  }),
}));
vi.mock("../ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../ui")>()),
  useLoad: () => ({ data: [], error: "", reload: vi.fn() }),
}));

it("distinguishes saved reference source from the configured execution routes", () => {
  const html = renderToStaticMarkup(
    <UIContext.Provider
      value={{
        lang: "en",
        theme: "light",
        t: (_zh, en) => en,
        notify: vi.fn(),
        action: async (fn) => fn(),
      }}
    >
      <MemoryRouter>
        <ExperimentsPage />
      </MemoryRouter>
    </UIContext.Provider>,
  );
  expect(html).not.toContain("Python execution");
  expect(html).toContain(
    "Configure an execution command; saved code is reference only.",
  );
  expect(html).toContain("python experiment.py");
  expect(html).toContain("experiment:run");
  expect(html).toContain("class_weight_calibration");
  expect(html).toContain("Recorded seeds<strong>11, 22, 33</strong>");
  expect(html).toContain("Seeds<strong>1, 2, 3</strong>");
});
