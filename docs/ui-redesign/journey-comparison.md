# Actual before/after journey measurements

Both variants used real Chromium interactions and the same isolated API and normal worker at `127.0.0.1:18340`. The original frontend was the clean audited `ff16c3f656f94d053d1bba65c6b3a07a5feaccde` source, served on port 18441 through a temporary Vite configuration with the installed dependencies. The redesigned frontend used its stable production build on port 18340. Each variant received a separate disposable project with the same two-node graph, instructions, provider-free local command and simple LaTeX source. Project creation and fixture preparation are outside the measured task paths.

A **click** is an explicit button, link or graph-node activation listed in the JSON. Text entry is recorded separately as one form edit; focus/typing keystrokes, scrolling and waits are not counted as clicks. A **main-surface switch** changes the primary working page/view. Opening a sidepane while retaining the graph is not a main-surface switch. **Selection actions** count explicit node/run choices. These are recorded user actions, not estimates of cognitive decisions.

| Task | Before | After | Actual result |
| --- | --- | --- | --- |
| Overview → graph → select command node → edit title → Save changes → Run node → inspect actual output | 4 clicks; 1 main-surface switch; 1 node selection; 1 form edit | 4 clicks; 1 main-surface switch; 1 node selection; 1 form edit | PASS in both. The saved title was read back from the graph; the real local command completed and produced `journey actual local output`. The run dock opened without an additional click. |
| Select Goal → edit instructions → save → manuscript → Check evidence → Compile PDF → download → return to Goal graph | 7 clicks; 2 main-surface switches; 1 node selection; 1 form edit | 7 clicks; 0 main-surface switches; 1 node selection; 1 form edit | Before PARTIAL: the real check and worker compile completed, but the only Export control returned a source ZIP and no Current PDF control existed. After PASS: graph stayed mounted, the actual PDF downloaded, and the selected Goal returned with its saved instructions. |

The second path's count includes the baseline Export attempt. It is not a successful baseline current-PDF download and must not be described as one. The two real builds show equal click counts for these paths; the measured improvement is retaining research context and exposing an explicit correct PDF export.

## Exact identities and evidence

| Identity | Before | After |
| --- | --- | --- |
| Project | `4f442d6a-36de-4f39-818f-4b6c147800d7` | `3b2ac9c2-4ca4-428a-8079-16484ce17bc7` |
| Goal node | `b67efce1-d395-5f86-9ced-ac4ce3837636` | `d556da0e-d4bf-5f5f-a5a8-6a4aea1a08c6` |
| Command node | `1c2530b9-e970-550c-8800-0af393c12463` | `158be3ff-dd01-5769-8f78-14feab6e37ad` |
| Actual command run | `a65b87b2-68e3-419c-8461-69d1d2075dbd` | `6333e9e0-35aa-4250-ad83-9ab8dede2e91` |
| Actual paper compile run | `104f3e28-1461-477b-96db-0c404eb06976` | `2a3033e5-1087-4b06-895e-7be622f69680` |
| Paper/compiled revision | `1 / 1` | `1 / 1` |
| Export | `200 application/zip`; generic Export; no PDF control | `200`; body `{format:"pdf", expected_revision:1}`; 4,575 PDF bytes with `%PDF-` header |
| Browser page errors | 0 | 0 |

The exact action lists, request data and readbacks are [before-journeys.json](journey-evidence/before-journeys.json) and [after-journeys.json](journey-evidence/after-journeys.json). [before-trace.zip](journey-evidence/before-trace.zip) and [after-trace.zip](journey-evidence/after-trace.zip) retain browser traces. The [current PDF](journey-evidence/after-current-manuscript.pdf) and screenshots in the same directory retain the observed artifacts. Both final journey commands exited 0. Execution logs were recorded as `before-execution.log` and `after-execution.log`. These files are absent from the repository. The final after path additionally waits for the selected Goal inspector and reads its saved instructions and r1 directly from the graph before opening the manuscript. An earlier attempt typed during inspector replacement; its accepted request retained the original instructions, so that attempt was superseded instead of being counted as a successful Goal edit.

The projects were removed after collecting this evidence. These are actual browser/API/worker checks of editing, routing, local execution and compilation. The simple command and manuscript are marked fixtures. No provider, paid/model-backed generation, scientific experiment, acceptance judgment or independent scientific verification ran. Timing comparisons between original Vite and after production were not made.
