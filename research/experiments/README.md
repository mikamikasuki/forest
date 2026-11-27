# Executable research case

This is a mechanism replication of probability correction after class weighting,
not a newly invented calibration algorithm. It uses the full public UCI Adult and
Bank Marketing sources, removes exact duplicates, and excludes Bank's post-call
duration feature. All reported scores come from saved observation predictions.

Run through a FOREST experiment node for persisted scheduling and cancellation.
The same computational entry point can also be run from the repository root:

```sh
.venv/bin/python -m research.experiments.case --output /path/to/isolated/run
```

An optional JSON file passed with `--config` overrides `DEFAULT_CONFIG`. The
default uses protocol version 2, three preselected training/calibration seeds,
ten methods, two CPU threads, and 60/20/20 train/calibration/test sizes. A fixed
test split is shared across all repetitions and never enters fitting. Each additional seed costs approximately
one more pair of dataset fits; actual costs are always measured in `training.json`.

Stages are independently callable: `prepare`, `train`, `evaluate`, and `collect`.
`prepare` records a SHA-256 checksum for each dataset CSV. Training checks the
bytes against those checksums before it updates any training artifacts. Run
`prepare` again if a CSV changed or a prior preparation has no checksum.
`--stage evaluate` rebuilds the prediction CSV and statistics from saved `.npz`
probabilities without training. `research.analysis.statistics.analyze` recomputes
statistics directly from a prediction CSV; its `confidence`, `ece_bins`,
`bootstrap_samples`, candidate, and comparison baselines are editable.

The primary contrast compares a fitted balanced logistic model before and after
subtracting its known class-weight log odds. Unweighted logistic regression and
histogram gradient boosting quantify stronger alternatives; learned intercept,
sigmoid slope/intercept, correction-strength perturbations, and restricted
calibration budgets identify the mechanism and label cost. Regularization is
selected on an inner split of training data with preprocessing fitted only on
inner-training rows, then models refit all training rows. Calibration labels
are exclusive to learned calibrators. Analytic correction needs training labels
for the base classifier but no additional calibration labels. Every logistic
method's timing includes the shared internal regularization-selection cost.

Object-cluster bootstrap intervals account for source rows that reappear across
test splits. They condition on the fitted models and do not treat overlapping
training sets as independent repetitions. Seed ranges/standard deviations are
descriptive, and all requested comparison tests receive Holm adjustment.

Artifacts include source metadata, split indices, actual configuration, package
versions, fitting times, convergence diagnostics, per-observation probabilities,
independently recomputed metrics, and comparison intervals. No metric comes from
a language model. `verify_reproduction(first, second)` requires exact prediction
equality and excludes wall-clock timing.

`research.figures.render.render_figure` exports real SVG/PDF/PNG figures and
editable source. `research.paper.manuscript.generate_paper` binds metric macros
to JSON pointers and generates LaTeX/BibTeX; `compile_paper` runs a real Tectonic or
pdfLaTeX toolchain and reports its exit status. `check_paper` checks missing
citations, figures, placeholders, and changed bound metrics. These are executable
consistency checks, not a peer-review or acceptance verdict.

Research design followed the ARS-Codex experiment workflow's experimental-duty,
statistical-fallacy, and reproducibility checks. The implementation is original;
the external skill's noncommercial source was not copied into the product.

Sources:

- [UCI Adult](https://archive.ics.uci.edu/dataset/2/adult)
- [UCI Bank Marketing](https://archive.ics.uci.edu/dataset/222/bank+marketing)
- [Menon et al., 2013](https://proceedings.mlr.press/v28/menon13a.html)
- [Guo et al., 2017](https://proceedings.mlr.press/v70/guo17a.html)
- [Loffredo et al., 2024](https://proceedings.mlr.press/v235/loffredo24a.html)
- [Liu, 2026: adjacent resampling study](https://arxiv.org/abs/2606.29720)
