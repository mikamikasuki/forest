# Statistical analysis and paper presentation

FOREST turns identified experiment results into statistical tables, measured curves, paired-effect plots and evidence-grounded explanations during manuscript generation. Outputs include editable LaTeX tables, vector PDF/SVG figures, PNG previews and reproducible plotting code.

## Supply experiment results

Save a `statistics` object in the run's `metrics.json`. Each observation identifies its dataset, method, metric, measured value and repetition or test-object identity. Preserve experimental conditions and measured checkpoints explicitly.

```json
{
  "statistics": {
    "observations": [
      {
        "dataset": "dataset-a",
        "method": "baseline",
        "metric": "accuracy",
        "value": 0.82,
        "seed": 0,
        "x": 1000,
        "condition": {"training_protocol": "fixed"}
      }
    ],
    "metrics": {
      "accuracy": {
        "direction": "higher",
        "unit": "fraction",
        "precision": 3
      }
    },
    "design": {
      "datasets": ["dataset-a", "dataset-b"],
      "methods": ["baseline", "proposed"],
      "metrics": ["accuracy"],
      "seeds": [0, 1, 2, 3, 4],
      "x": [1000, 2000, 4000],
      "conditions": [{"training_protocol": "fixed"}]
    },
    "axes": {"x": {"label": "Training examples", "unit": "examples"}},
    "uncertainty": {"type": "sd", "unit": "seed"}
  }
}
```

The example shows the schema, not a complete experiment. Save all observations declared by the design. Results can span several runs; select the complete set of relevant run IDs when creating a figure or manuscript. FOREST merges their observations before computing statistics and checks the expected dataset × method × metric × condition × checkpoint × repetition matrix.

`x` is a measured variable such as training examples, optimization steps, noise level or compute budget. A random seed identifies a repetition. Omit `x` for a comparison at one fixed condition.

Display precision uses significant digits and never changes the stored measurements. Units retain their original meaning: an accuracy fraction remains a fraction unless the experiment explicitly exports percentage values. Unknown metrics require their own direction and unit.

Existing identified `per_seed`, `summary` and comparison artifacts are also supported. An aggregate summary remains an aggregate; it does not become another independent repetition. Identical reanalysis results preserve their sources without doubling sample counts, and conflicting identities are rejected.

## Statistical definitions

The default uncertainty is descriptive SD across seeds. SE, Student-t confidence intervals and bootstrap intervals require an explicit independence declaration:

```json
{"type": "t_ci", "unit": "seed", "independent": true, "confidence": 0.95}
```

Use `unit_id` for per-object measurements and declare `uncertainty.unit` as `seed` or `unit_id`. Repeated measurements are averaged within the declared unit before computing uncertainty. Object intervals condition on the supplied seeds; seed intervals condition on the evaluated datasets and objects. A single unit has no estimated SD or interval.

Paired comparisons match actual unit identities within each dataset, metric, condition and checkpoint. Declare them in `statistics.comparisons`:

```json
[
  {
    "baseline": "baseline",
    "candidate": "proposed",
    "uncertainty": {"type": "t_ci", "unit": "seed", "independent": true}
  }
]
```

Positive improvement always favors the candidate, accounting for the metric direction. Inferential tests require their own supported assumptions; a bootstrap interval does not automatically supply a valid p-value. Declared test families support Holm adjustment. Point-estimate ranking, confidence intervals and statistical significance remain separate concepts.

## Manuscript workflow

Invoke `paper_generate` with the relevant completed run IDs. The statistical path prepares figures and a complete table catalog before the writer drafts the paper:

1. Validate identities, sources, statistical units and design coverage.
2. Compute estimates, uncertainty and declared paired comparisons across the selected runs.
3. Render every measured metric at publication size, with complete comparisons and consistent method encodings.
4. Prepare grouped tables, paired-effect tables and all measured checkpoint tables. The writer places the main argument in the paper and detailed checkpoint matrices in appendices when appropriate.
5. Draft captions and adjacent interpretations around the strongest supported conditional finding and the experiment's argumentative duty.
6. Review statistical explanations against the actual results, then validate numeric bindings, layout and compilation.

With a configured provider, visual reviewers inspect actual rendered candidates for evidence fidelity, statistical clarity, coverage, readability and caption fit. Presentation repairs preserve observations and statistical definitions. Unaccepted candidates do not become approved figures.

Tables use grouped dataset/metric headers, explicit direction and units, consistent precision, aligned numeric cells, readable horizontal panels and repeated headers for continuation tables. Statistical cells stay intact. Notes define the sampling unit, count, uncertainty and meaning of boldface. Bold identifies the best point estimate within its comparison; it does not assert significance.

Curves retain all actual checkpoints, methods and uncertainty endpoints. Confidence bands are pointwise unless a simultaneous procedure is explicitly supplied. A single checkpoint is rendered as points with intervals. Paired-effect panels include a zero reference; comparison matrices preserve the full selected dataset/method scope. Large facet grids are paginated at readable size rather than reduced to tiny text.

New plot bindings record each metric's unit and optimization direction alongside its numeric values. Plotting verifies the recorded metadata before rendering. Older saved figures can still be opened. Regenerate their data from the original runs to attach the unit and direction bindings.

Explanations state what the comparison establishes, under which condition and with what practical value. Source-supported qualifications stay precise. Captions do not narrate the analysis process, invent a mechanism or turn a local result into an aggregate claim. Measured statements use the same numeric references as their tables.

### Calibration curves

Calibration figures use saved per-observation predictions with `dataset`, `method`, `seed`, `y_true` and `probability`. Include `sample_id` or `unit_id` when available to verify shared test-object identities across methods.

Datasets receive separate reliability and bin-count panels. Each seed is binned separately and receives equal weight in the seed-level summary. Empty bins remain explicit and break connecting lines. The count panels include zero-count seeds; reliability intervals report their populated-seed counts. The default is descriptive seed SD; CI requires declared independent seeds. Dataset or seed subsets require an explicit `selection_scope`, and method subsets require `comparison_scope`.

### Direct Python use

```python
from research.paper.evidence import collect_evidence
from research.paper.statistical_workflow import prepare_statistical_presentation

evidence = collect_evidence([
    {"id": run_id, "status": "completed", "directory": run_directory}
    for run_id, run_directory in selected_runs
])
prepared = prepare_statistical_presentation(
    evidence, output_directory, layout={"columns": "double"}, client=model_client
)
```

For authored manuscript JSON, use `statistics_spec` table blocks from `prepared["statistical_presentation"]["tables"]`. Add the caption, argumentative duty, local cross-reference and paragraph anchor; the renderer materializes the cells from bound statistics. Do not replace the declarations with manually copied measured numbers.

The selected figure bundle retains its data, plotting source, style, statistical report and caption context. Re-running its `plot.py` with the installed FOREST environment reproduces the saved display. Manuscript checks also verify derived results against the copied original measurements and detect changed method identities, stale values or omitted comparisons.
