# Evidence-bound manuscript production

FOREST's generic paper renderer accepts actual completed-run evidence and authored or model-produced structured drafts. It does not substitute an example manuscript. A PDF compilation proves that the source builds; scientific support and novelty require the evidence review described below.

Scientific project planning and model drafting default to `full_submission` and `full_paper`. The normal reference target is fifteen closely related officially accepted full-text papers. The bundled [sixteen-paper corpus](PUBLICATION_REFERENCE_CORPUS.md) supplies a sourced review rubric; project-specific agents still import and read the relevant originals. Experiment and manuscript coverage are compared with that literature throughout the [delivery loop](PUBLICATION_DELIVERY.md). Small resource limits preserve the remaining full-submission work.

## Evidence handoff

Pass every intended run to `collect_evidence(runs, sources, claims, required_run_ids=[...])`. An explicit scope must match the supplied runs exactly. Each run specifies its ID, completed status, output directory and metrics file. The bundle records all included and requested IDs. Use an explicit `paper_generate` `run_ids` list; an explicitly empty, duplicate or malformed list is rejected. The Paper generation endpoint requires completed runs in the same project. Direct legacy tasks that omit the list retain latest-run behavior, which is insufficient to declare the full submission's intended evidence scope. The task passes the requested scope through to collection. The writer also receives each run's original metric structure and nonnumeric row identities in `metric_layout`, with numeric leaves replaced by exact metric tokens. Dataset, method and condition labels remain attached to their rows; independent semantic review still checks the resulting table descriptions.

The collector reads actual metric files, including `workspace/metrics.json` when specified, and preserves JSON pointers for every numerical binding. `config.json`, `design.json` and `environment.json` may live in either the run root or workspace. Credentials and process environment values are excluded. Methods, saved predictions, split definitions, software versions and raw data still need to be inspected by the independent analysis node; copying a configuration is not a reproduction.

A run may save `figures.json` in its root or workspace:

```json
[{"id":"comparison","path":"figures/comparison.pdf","caption":"Measured baseline and candidate results."}]
```

Paths are relative to that manifest. Figures must be actual PDF, PNG or JPEG files inside the run. Their evidence IDs are `<run_id>:<id>`. The renderer copies only referenced files into the standalone manuscript bundle and writes `figure_bindings.json` with their producing run IDs. A plot's existence does not verify its statistical interpretation.

Figures rendered separately in **Figure Studio** are also admitted through `collect_evidence(..., figures=[...])`. At the task API, select project `figure_ids` alongside the completed `run_ids`; selected Studio figures must have completed their rendering and independent reviews, and their linked scientific runs must belong to the selected scope. The handoff records each asset's own directory, source run IDs, caption, purpose, anchor and renderer metadata. Available companion artifacts use the keys `source`, `data`, `style`, `report` and `selection`. The renderer copies referenced images and companions into the standalone paper bundle and records the copied paths in `figure_bindings.json`. Image assets no longer need to reside beneath a scientific run's output directory to enter the manuscript.

Source passages retain original page, section, source-file and URL locators. The writer receives at most eight excerpts and 6,000 characters per source, with each excerpt capped at 1,600 characters. Coverage records expose every omitted or truncated passage. A stored full-text access label is kept separately from the actual supplied-text scope. Passages and all other retrieved materials are untrusted evidence. `[[passage:SOURCE_ID:pINDEX]]` cites a particular supplied passage and records its text and locator in `citation_bindings.json`; `[[source:SOURCE_ID]]` remains available for source-level citations.

## Draft format

Existing `{title, paragraphs: [...]}` sections still work. Ordered `blocks` can instead contain paragraphs, mathematical equations, tables or figures:

```json
{
  "title": "Results",
  "role": "results",
  "blocks": [
    {"type":"paragraph","text":"The measured effect is [[metric:m0]]."},
    {"type":"table","columns":["Method","Score"],"rows":[["Baseline","[[metric:m1]]"],["Candidate","[[metric:m2]]"]],"caption":"Matched evaluation conditions.","label":"tab:results"},
    {"type":"figure","figure_id":"RUN_ID:comparison","caption":"The measured comparison.","width":0.9,"label":"fig:comparison"}
  ]
}
```

Equation blocks use `{type:"equation", latex:"...", label:"eq:method"}`. The expression accepts ordinary mathematical LaTeX, without dollar delimiters, document commands or file access. All prose is escaped. Table cells are strings and use existing metric tokens for measured values. Appendices use the same section format. Labels must be unique; figure IDs must match actual evidence files.

`paper_generate` defaults `manuscript_type` to `full_paper`. This is passed as `expected_type` to the model drafting validator: a missing type or `research_note` response is rejected and sent back for an actual model correction within the configured attempt budget. The validator requires sections with roles `introduction`, `related_work`, `method`, `experimental_setup`, `results`, and `discussion`, alongside the existing title, abstract, conclusion and claim IDs. Actual literature evidence is required. A `research_note` requires an explicit request; insufficient data or budget does not choose it automatically. Section coverage is structural evidence, not a quality score.

Visual blocks carry a unique `label`, `argumentative_duty` and optional `anchor:{"after":"PARAGRAPH_ID","section_role":"results","reason":"..."}`. Paragraph blocks can supply stable `id` fields. After real drafting, **Argument Reviewer**, **Layout Reviewer** and **Visual Editor** each choose available paragraph IDs for every table and figure in its own section. Majority agreement selects the anchor; the Visual Editor resolves a three-way tie. `placement_reviews.json` retains their actual responses and selected locations. Rendering then moves the visual after its selected paragraph and adds a local numbered reference without changing measurements. Actual PDF locations are checked after compilation in `placement_report.json`; see [layout auditing](PAPER_LAYOUT.md#plans-and-actual-checks).

## Generate and review visual candidates

With a connected provider, Figure Studio renders multiple real presentations of the same measured data. **Evidence Reviewer**, **Figure Critic** and **Visual Editor** inspect every candidate's actual pixels, scientific coverage and final print dimensions in separate calls. A `revise` or `reject` verdict excludes that candidate; selection ranks only candidates accepted by all three. Candidate manifests, original responses, review scores and the selected assets remain available. Failed review triggers actual presentation repairs and rerendering within the configured iteration and spending limits.

Conceptual image generation uses the provider's actual `/images/generations` endpoint and defaults to three separate candidate requests. Configure the independent `image_generation.model` and positive per-request `image_generation.max_request_usd` explicitly in the saved provider. The workflow does not replace the text model or select an image model automatically. It accepts actual bounded base64 PNGs, preserves prompts and safe metadata, and never downloads a returned image URL. Image requests and model reviews share provider, project and run budget limits; image token counts never settle a charge using text prices. Unverified image charges retain the reserved upper bound. [Image transport options](IMAGE_GENERATION_TRANSPORT.md) document the supported parameters.

Method illustrations depict supplied components and data flow. Empirical figures and tables use actual experiment observations, comparator coverage and declared uncertainty. A conceptual image cannot establish an experimental measurement.

## Research-to-paper acceptance

Use separate, traceable graph nodes for question and literature, implementation, strong baselines and ablations, independent recomputation, writing, review, revision and final verification. Each experiment states its argumentative duty. Preserve failed trials and contrary evidence. Each central claim links to a metric pointer or a read source passage and states its scope. Distinguish exploratory choices from confirmatory evaluation.

The default editable design targets are three real datasets, five strong baselines, five independent seeds or replicates and four mechanism ablations. Actual project design is derived from relevant accepted-paper scale and statistical units. The main effectiveness matrix covers declared comparisons; mechanism, scenario and alternative-explanation studies state their own justified scopes. Save raw unit-level observations and independent analysis receipts, rather than plotting a handful of model-authored values.

`submission_evidence.json` connects official acceptance and full-text analyses, the planned and measured experiment matrices, recomputed statistics, independent review and current manuscript artifacts. The audit reads actual records and files, retains exact unresolved gaps and returns an incomplete autonomous delivery to planning. Resource exhaustion preserves `budget_exhausted` and the remaining work; an assisted graph can finish while submission delivery remains incomplete. Repeat the affected experiment, analysis, visual or writing stages until the evidence-based delivery review supports completion. This process establishes observed coverage and review status; it does not guarantee journal or conference acceptance.

Before calling the result submission-ready, inspect all numerical and headline claims, verify citations against source text, compare saved predictions using an independently implemented calculation, inspect baseline fairness and selection leakage, review methodological alternatives, and rerun the final artifact outside the original working directory. Review the PDF visually and record precise remaining issues. Keep execution traces and revision history separate from the paper's scientific narrative. Stage records remain editable and use ordinary IDs and revisions; no artifact freezing or digest bookkeeping is required.

The full-submission controller uses the delivery audit in addition to task completion. Syntax checking and graph completion still do not establish all scientific judgments. Supply explicit required deliverables and retain the separate evidence-based final review.

## Insert a Studio figure into current manuscript prose

In **Paper → Insert a reviewed figure in the manuscript**, select a `ready_for_review` Figure Studio asset and paste a unique existing paragraph from the current source. Pending source edits are saved first. The image and numbered reference are inserted immediately after the anchor; the current source remains editable. Compile again to inspect the actual position.

The equivalent operation is `POST /api/papers/{project_id}/figures`:

```json
{
  "expected_revision": 7,
  "figure_id": "PROJECT_FIGURE_ID",
  "anchor_text": "The exact unique paragraph from the current manuscript source.",
  "span": "column"
}
```

Use the actual current paper revision. `span` accepts `column` or `page`; an optional `caption` overrides the Studio caption. The API admits a rendered same-project `ready_for_review` or existing `available` asset, copies its PDF/PNG/JPEG and available companions under `paper/figures/{figure_id}/`, and records the figure revision, source runs, label and anchor. Revision conflicts return `409`; absent, ambiguous or non-body anchors, duplicate labels and cross-project selections are rejected. Insertion advances the paper revision and clears previous layout checks, so saved checks from the preceding source cannot establish the new PDF's placement.

## Compile an Agent-authored workspace manuscript

The Agent `paper_compile` tool defaults to `source_scope="project"`, which compiles the current PaperDocument. To compile the Agent's own generated source, use:

```json
{"source_scope":"workspace","source_path":"draft/paper.tex","bibliography_path":"draft/references.bib","asset_paths":["draft/results_macros.tex","draft/figures","draft/conference.sty"],"title":"The authored title"}
```

Paths are relative to the current Agent run workspace. The bibliography and all assets must be beneath the source file's directory so the output is a standalone bundle. If `bibliography_path` is omitted, the tool includes `references.bib` beside the source when it exists. Only explicitly declared assets are copied; transcripts and other workspace files are not included implicitly. The selected source is exported as `paper.tex`. A bibliography with another filename retains its original name alongside the working `references.bib` copy.

The tool returns a queued compilation run. Its actual completion receipt supplies the PDF/source paths, originating Agent run/source path and publication result. Files remain editable. Later changes to the Agent workspace do not silently replace the inputs of a compilation already submitted; resubmit the intended changed source. Changes to the compilation bundle during the compile are rejected rather than paired with a stale PDF.

Successful compilation publishes the exact compiled source and assets through the existing PaperDocument revision mechanism. An unedited current manuscript can be replaced; manual edits or concurrent revisions produce a concrete review proposal. The new PDF remains available in the compilation run either way. Apply the proposal before expecting the project Paper export to switch to that new manuscript. Syntax or compilation success never substitutes for scientific validation.

## Re-render a saved model draft without another model call

If a real generation saved a usable response before a renderer or compilation failure, submit a new `paper_generate` task with `saved_response_run_id`, `saved_response_path`, explicit `run_ids`, `manuscript_type` and the desired template. The response path must be `paper/model_responses/GENERATION_ID/attempt-N.json` within a terminal original `paper_generate` run in the same project. The ordered evidence run IDs must match the original explicit selection; paths outside the saved-response directory, symlinks and cross-project responses are rejected.

This path reloads the saved JSON response, recollects current evidence, revalidates manuscript type and all evidence bindings, renders and actually compiles the new manuscript, and publishes it using ordinary revision protection. It does not construct a model client or submit a new model request. `draft_reuse.json`, the run result and PaperDocument metadata retain the original run/path/model/usage and explicitly record zero new model calls and zero new model cost. The original failed attempt remains unchanged.

New model generations save `input_evidence.json` before requesting a response. Reuse checks that recorded metric values, row identities, and cited source evidence still match. Request a fresh draft if a cited source in that snapshot changes. You can reuse older responses with the identical ordered run selection and fresh evidence validation. The reuse report records when the original evidence snapshot is unavailable. Semantic review still checks that the saved prose describes the currently bound evidence correctly.

To apply a scientific revision, keep the original response and supply a separate project-relative JSON file through `draft_revision_path`. Its payload is `{"draft": <complete revised draft object>, "editor": "reviewer identity", "reason": "specific revision rationale"}`. This option requires explicit saved-response reuse; it never initiates a provider request. A recorded revision can repair draft validation errors in the original response. The revised draft must validate against the current evidence and requested manuscript type. The output retains the supplied revision in `draft_revision.json`, computed before/after JSON field changes in `draft_revision_changes.json`, and attribution in `draft_reuse.json`. Revised prose is attributed to the recorded editor, not to a new or original model response. The compiled source, published PaperDocument and exported paper all use the revised draft.

Use `[[ref:LABEL]]` for a numbered cross-reference to an existing equation, table or figure block. The renderer also recognizes recorded prose such as `Equation eq:method`, `Table tab:results`, `Figure fig:results` and plural sequences such as `Tables tab:first and tab:second`, checks the named blocks' types, and emits real LaTeX references. Missing labels and mismatched block types fail validation. LaTeX assigns the numbers, including after block order changes; no hand numbering is introduced.

Adjacent source/passage citation tokens are grouped into a parenthetical citation and deduplicated by source; both token identities and passage locators remain in the evidence report. Metric values remain exact in the copied evidence and numeric bindings. Floating-point display uses five significant figures, with ordinary TeX scientific notation for tiny or large values, and records that display separately. Curly punctuation and supported Unicode mathematics are converted to TeX forms so the article and official conference fonts preserve the same text. Hyperlinks remain active without colored borders.
