"""Class-weight correction: an executable, falsifiable replication study.

Separate stages permit reevaluation and reanalysis without retraining. Nothing
in this module uses model-generated metrics or a database.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import platform
import time
from uuid import uuid4
import zipfile

import httpx
import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.special import expit
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from threadpoolctl import threadpool_limits

DEFAULT_CONFIG = {"protocol_version": 2, "fixed_test_seed": 20260927, "tuning_fraction": 0.2, "datasets": ["adult", "bank"], "seeds": [7, 19, 43], "train_fraction": 0.6, "calibration_fraction": 0.2, "c_grid": [0.1, 1.0, 10.0], "max_iter": 1200, "hgb_iterations": 150, "bootstrap_samples": 1000, "bootstrap_seed": 20260927, "n_threads": 2, "primary_baseline": "balanced_raw", "candidate": "prior_corrected", "meaningful_brier_gain": 0.002, "calibration_budgets": [64, 256], "correction_strengths": [0.5, 1.5]}
SOURCES = {"adult": {"url": "https://archive.ics.uci.edu/static/public/2/adult.zip", "doi": "10.24432/C5XW20", "license": "CC BY 4.0", "target": "income > 50K", "excluded_features": []}, "bank": {"url": "https://archive.ics.uci.edu/static/public/222/bank+marketing.zip", "doi": "10.24432/C5K306", "license": "CC BY 4.0", "target": "term deposit subscription", "excluded_features": ["duration (known only after the call)"]}}
SPLIT_CONFIG_KEYS = ("datasets", "seeds", "train_fraction", "calibration_fraction",
                     "fixed_test_seed", "tuning_fraction", "protocol_version")
FIT_CONFIG_KEYS = (*SPLIT_CONFIG_KEYS, "c_grid", "max_iter", "hgb_iterations",
                   "calibration_budgets", "correction_strengths", "n_threads")


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def _fit_configuration(config):
    return {key: list(config[key]) if isinstance(config[key], tuple) else config[key]
            for key in FIT_CONFIG_KEYS}


def configuration(config=None):
    result = dict(DEFAULT_CONFIG)
    if config:
        result.update(config)
    if not result["datasets"] or not set(result["datasets"]) <= set(SOURCES):
        raise ValueError("Datasets must be adult and/or bank")
    if not result["seeds"] or len(set(result["seeds"])) != len(result["seeds"]):
        raise ValueError("Provide at least one distinct seed")
    if not 0 < result["train_fraction"] < 1 or not 0 < result["calibration_fraction"] < 1 - result["train_fraction"]:
        raise ValueError("Train/calibration fractions must leave a nonempty test set")
    if not result["c_grid"] or min(result["c_grid"]) <= 0:
        raise ValueError("Regularization C values must be positive")
    if result["protocol_version"] != 2:
        raise ValueError("This implementation executes protocol_version 2; preserve earlier run artifacts separately")
    if not 0 < result["tuning_fraction"] < 1:
        raise ValueError("The internal training-validation fraction must lie in (0,1)")
    return result


def split_indices(y, seed, config):
    """One untouched test set; repeats only repartition the remaining pool."""
    y = np.asarray(y)
    pool, test = train_test_split(np.arange(len(y)), train_size=config["train_fraction"] + config["calibration_fraction"], stratify=y, random_state=config["fixed_test_seed"])
    train, calibration = train_test_split(pool, train_size=config["train_fraction"] / (config["train_fraction"] + config["calibration_fraction"]), stratify=y[pool], random_state=seed)
    tuning_train, tuning_validation = train_test_split(train, test_size=config["tuning_fraction"], stratify=y[train], random_state=seed + 2)
    return {"train": train, "calibration": calibration, "test": test, "tuning_train": tuning_train, "tuning_validation": tuning_validation}


def _download_dataset(name, cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive = cache_dir / f"{name}.zip"
    if not archive.exists():
        print(f"Downloading UCI {name}: {SOURCES[name]['url']}", flush=True)
        response = httpx.get(SOURCES[name]["url"], timeout=90, follow_redirects=True)
        response.raise_for_status()
        archive.write_bytes(response.content)
    with zipfile.ZipFile(archive) as z:
        if name == "adult":
            names = ["age", "workclass", "fnlwgt", "education", "education_num", "marital_status", "occupation", "relationship", "race", "sex", "capital_gain", "capital_loss", "hours_per_week", "native_country", "target"]
            train = pd.read_csv(io.BytesIO(z.read("adult.data")), names=names, skipinitialspace=True, na_values="?")
            test = pd.read_csv(io.BytesIO(z.read("adult.test")), names=names, skipinitialspace=True, na_values="?", skiprows=1)
            frame = pd.concat([train, test], ignore_index=True)
            frame["target"] = frame["target"].str.rstrip(".").eq(">50K").astype(int)
        else:
            nested = zipfile.ZipFile(io.BytesIO(z.read("bank.zip")))
            frame = pd.read_csv(io.BytesIO(nested.read("bank-full.csv")), sep=";")
            frame["target"] = frame.pop("y").eq("yes").astype(int)
            frame = frame.drop(columns=["duration"])
    original_rows = len(frame)
    frame = frame.drop_duplicates().reset_index(drop=True)
    return frame, original_rows


def prepare(output_dir, config=None):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    config = configuration(config)
    _write(output / "config.json", config)
    cache = Path(config.get("cache_dir") or os.environ.get("FOREST_DATASET_CACHE", str(Path.home() / ".cache" / "forest" / "datasets")))
    metadata = {}
    for name in config["datasets"]:
        frame, original_rows = _download_dataset(name, cache)
        frame.to_csv(output / f"data_{name}.csv", index=False)
        metadata[name] = {**SOURCES[name], "original_rows": original_rows, "rows": len(frame), "features": len(frame.columns) - 1, "prevalence": float(frame.target.mean()), "exact_duplicates_removed": original_rows - len(frame), "protocol_version": 2, "split_protocol": "combined original dataset; one fixed untouched stratified test set, repeated train/calibration partitions of remaining pool; C selection uses only an internal training split; not the original UCI fixed holdout"}
        for seed in config["seeds"]:
            np.savez_compressed(output / f"split_{name}_{seed}.npz", **split_indices(frame.target, seed, config))
    _write(output / "datasets.json", metadata)
    _write(output / "environment.json", {"python": platform.python_version(), "platform": platform.platform(), "processor": platform.processor(), "scikit_learn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__, "execution": "CPU", "n_threads": config["n_threads"]})
    _write(output / "design.json", {"material_passport": {"kind": "experiment_plan", "source": "public UCI observations", "status": "planned", "human_read": False}, "question": "Does analytic correction recover at least 80% of held-out sigmoid calibration's Brier gain over balanced logistic regression, without calibration labels?", "candidate": "Subtract known class-weight log odds from the balanced model's logit", "primary_outcome": "Brier score, lower is better", "meaningful_gain": config["meaningful_brier_gain"], "status": "exploratory mechanism replication; no algorithmic novelty claimed", "duties": {"unweighted_lr": "rule out the alternative that avoiding weighting solves the problem", "hgb": "quantify headroom from a strong nonlinear model", "balanced_raw": "measure the distortion induced by inverse-frequency weighting", "prior_corrected": "test analytic mechanism without calibration labels", "sigmoid": "compare against learned slope/intercept calibration", "intercept_only": "separate offset correction from slope correction", "strength_ablation": "test whether correction magnitude, not arbitrary postprocessing, explains the gain", "calibration_budget": "measure the value of learned calibration when labels are scarce"}, "prior_estimate": {"label": "ESTIMATED before execution", "best_estimate": "Analytic correction removes most weighting distortion; it is largely redundant with ordinary unweighted logistic regression.", "probability_any_gain_over_balanced": [0.90, 0.98], "probability_meaningful_gain_over_unweighted": [0.10, 0.25], "probability_publishable_standalone_finding": [0.05, 0.15], "confidence": "Medium", "why": "Class weighting shifts the Bayes-optimal log odds by a known constant.", "against": "Regularization and model misspecification change slopes, which a fixed offset cannot repair.", "decisive_unknown": "The residual slope distortion and small-sample calibration variance on these tasks.", "cheapest_resolution": "One fixed split with raw, analytic, unweighted, and sigmoid models."}})
    return metadata


def _preprocessor(frame):
    numeric = list(frame.select_dtypes(include=np.number).columns)
    categorical = [c for c in frame.columns if c not in numeric]
    return ColumnTransformer([("numeric", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), numeric), ("categorical", make_pipeline(SimpleImputer(strategy="most_frequent"), OneHotEncoder(handle_unknown="ignore", sparse_output=False)), categorical)], sparse_threshold=0)


def train(output_dir, config=None):
    output = Path(output_dir)
    stored_config = json.loads((output / "config.json").read_text())
    if stored_config.get("protocol_version", 1) != 2:
        raise ValueError("Prepare a new protocol_version 2 run; earlier scientific conditions must remain separate")
    config = configuration({**stored_config, **(config or {})})
    # Train must use precisely the prepared splits/configuration.
    prepared = json.loads((output / "config.json").read_text())
    for key in SPLIT_CONFIG_KEYS:
        if config[key] != prepared[key]:
            raise ValueError(f"{key} changed: run prepare before training")
    attempt_path = output / "training_attempt.json"
    attempt = {"id": uuid4().hex, "status": "pending", "fit_config": _fit_configuration(config)}
    _write(attempt_path, attempt)
    _write(output / "config.json", config)
    records = []
    with threadpool_limits(limits=int(config["n_threads"])):
        for name in config["datasets"]:
            frame = pd.read_csv(output / f"data_{name}.csv")
            y = frame.pop("target").to_numpy(int)
            for seed in config["seeds"]:
                begin = time.perf_counter()
                splits = np.load(output / f"split_{name}_{seed}.npz")
                tr, cal, te = splits["train"], splits["calibration"], splits["test"]
                candidates = []
                tuning_start = time.perf_counter()
                ti, tv = splits["tuning_train"], splits["tuning_validation"]
                tuning_prep = _preprocessor(frame.iloc[ti])
                xti = tuning_prep.fit_transform(frame.iloc[ti])
                xtv = tuning_prep.transform(frame.iloc[tv])
                for c in config["c_grid"]:
                    model = LogisticRegression(C=c, max_iter=config["max_iter"], random_state=seed).fit(xti, y[ti])
                    candidates.append((brier_score_loss(y[tv], model.predict_proba(xtv)[:, 1]), c, model))
                _, best_c, _ = min(candidates, key=lambda x: x[0])
                tuning_seconds = time.perf_counter() - tuning_start
                prep = _preprocessor(frame.iloc[tr])
                xtr = prep.fit_transform(frame.iloc[tr])
                xcal, xte = prep.transform(frame.iloc[cal]), prep.transform(frame.iloc[te])
                start = time.perf_counter()
                unweighted = LogisticRegression(C=best_c, max_iter=config["max_iter"], random_state=seed).fit(xtr, y[tr])
                unweighted_seconds = time.perf_counter() - start
                start = time.perf_counter()
                balanced = LogisticRegression(C=best_c, class_weight="balanced", max_iter=config["max_iter"], random_state=seed).fit(xtr, y[tr])
                balanced_seconds = time.perf_counter() - start
                logits_cal, logits_test = balanced.decision_function(xcal), balanced.decision_function(xte)
                prior = float(y[tr].mean())
                shift = float(np.log(prior / (1 - prior)))
                probabilities = {"unweighted_lr": unweighted.predict_proba(xte)[:, 1], "balanced_raw": expit(logits_test), "prior_corrected": expit(logits_test + shift)}
                timings = {"unweighted_lr": tuning_seconds + unweighted_seconds, "balanced_raw": tuning_seconds + balanced_seconds, "prior_corrected": tuning_seconds + balanced_seconds}
                calibration = {}
                start = time.perf_counter()
                sigmoid = LogisticRegression(C=1e6, max_iter=1000).fit(logits_cal.reshape(-1, 1), y[cal])
                probabilities["sigmoid"] = sigmoid.predict_proba(logits_test.reshape(-1, 1))[:, 1]
                timings["sigmoid"] = tuning_seconds + balanced_seconds + time.perf_counter() - start
                calibration["sigmoid_slope"] = float(sigmoid.coef_[0, 0])
                calibration["sigmoid_intercept"] = float(sigmoid.intercept_[0])
                start = time.perf_counter()
                offset = minimize_scalar(lambda b: np.mean(np.logaddexp(0, logits_cal + b) - y[cal] * (logits_cal + b)), bounds=(-12, 12), method="bounded")
                if not offset.success:
                    raise RuntimeError("Intercept calibration did not converge")
                probabilities["intercept_only"] = expit(logits_test + offset.x)
                calibration["learned_offset"] = float(offset.x)
                calibration["analytic_offset"] = shift
                timings["intercept_only"] = tuning_seconds + balanced_seconds + time.perf_counter() - start
                for alpha in config["correction_strengths"]:
                    method = f"prior_alpha_{alpha:g}"
                    probabilities[method] = expit(logits_test + float(alpha) * shift)
                    timings[method] = tuning_seconds + balanced_seconds
                for budget in config["calibration_budgets"]:
                    if budget >= len(cal) or budget < 4:
                        continue
                    subset, _ = train_test_split(np.arange(len(cal)), train_size=int(budget), stratify=y[cal], random_state=seed)
                    start = time.perf_counter()
                    fitted = LogisticRegression(C=1e6, max_iter=1000).fit(logits_cal[subset, None], y[cal][subset])
                    method = f"sigmoid_n{budget}"
                    probabilities[method] = fitted.predict_proba(logits_test[:, None])[:, 1]
                    timings[method] = tuning_seconds + balanced_seconds + time.perf_counter() - start
                start = time.perf_counter()
                hgb = HistGradientBoostingClassifier(max_iter=config["hgb_iterations"], learning_rate=0.08, max_leaf_nodes=31, l2_regularization=1.0, random_state=seed, early_stopping=False).fit(xtr, y[tr])
                timings["hgb"] = time.perf_counter() - start
                probabilities["hgb"] = hgb.predict_proba(xte)[:, 1]
                np.savez_compressed(output / f"probabilities_{name}_{seed}.npz", sample_id=te, y_true=y[te], **probabilities)
                run = {"dataset": name, "seed": seed, "protocol_version": 2, "train_n": len(tr), "tuning_train_n": len(ti), "tuning_validation_n": len(tv), "tuning_seconds_shared": tuning_seconds, "calibration_n": len(cal), "test_n": len(te), "train_prevalence": prior, "selected_c": best_c, "c_selection_labels": "internal training split only", "candidate_c_scores": [{"c": c, "brier": float(score)} for score, c, _ in candidates], "fit_seconds": timings, "calibration": calibration, "converged_unweighted": bool(np.max(unweighted.n_iter_) < config["max_iter"]), "converged_balanced": bool(np.max(balanced.n_iter_) < config["max_iter"]), "elapsed_seconds": time.perf_counter() - begin}
                records.append(run)
                _write(output / "training.json", records)
                print(json.dumps({"event": "training_completed", "dataset": name, "seed": seed, "seconds": round(run["elapsed_seconds"], 3), "methods": list(probabilities)}), flush=True)
    _write(attempt_path, {**attempt, "status": "completed"})
    return records


def evaluate(output_dir, config=None):
    output = Path(output_dir)
    stored_config = json.loads((output / "config.json").read_text())
    if stored_config.get("protocol_version", 1) != 2:
        raise ValueError("Re-evaluation requires a protocol_version 2 run. Analyze legacy predictions.csv directly to preserve its original protocol.")
    config = configuration({**stored_config, **(config or {})})
    attempt_path = output / "training_attempt.json"
    attempt_snapshot = attempt_path.read_text() if attempt_path.is_file() else None
    if attempt_snapshot is not None:
        attempt = json.loads(attempt_snapshot)
        if attempt["status"] != "completed":
            raise ValueError("The latest training attempt did not complete. Run train before evaluation.")
        if attempt["fit_config"] != _fit_configuration(config):
            raise ValueError("The training configuration changed. Run train with the desired fitting options.")
    frames = []
    for name in config["datasets"]:
        for seed in config["seeds"]:
            raw = np.load(output / f"probabilities_{name}_{seed}.npz")
            for method in raw.files:
                if method in {"sample_id", "y_true"}:
                    continue
                frames.append(pd.DataFrame({"dataset": name, "seed": seed, "sample_id": raw["sample_id"], "method": method, "y_true": raw["y_true"], "probability": raw[method]}))
    predictions = pd.concat(frames, ignore_index=True)
    if (attempt_path.read_text() if attempt_path.is_file() else None) != attempt_snapshot:
        raise ValueError("The training attempt changed during evaluation. Retry after training completes.")
    _write(output / "analysis_config.json", config)
    predictions.to_csv(output / "predictions.csv", index=False, float_format="%.17g")
    from research.analysis.statistics import analyze
    return analyze(output / "predictions.csv", output, config)


def collect(output_dir):
    output = Path(output_dir)
    return {"metrics": json.loads((output / "metrics.json").read_text()), "files": [{"path": str(p.relative_to(output)), "bytes": p.stat().st_size} for p in sorted(output.rglob("*")) if p.is_file()]}


def compare(run_dirs):
    return [{"output_dir": str(path), **json.loads((Path(path) / "metrics.json").read_text())} for path in run_dirs]


def run_case(output_dir, config=None):
    prepare(output_dir, config)
    train(output_dir, config)
    result = evaluate(output_dir, config)
    print(json.dumps({"event": "experiment_completed", "output_dir": str(output_dir), "prediction_rows": result["prediction_rows"]}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config")
    parser.add_argument("--stage", choices=["prepare", "train", "evaluate", "all"], default="all")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text()) if args.config else None
    {"prepare": prepare, "train": train, "evaluate": evaluate, "all": run_case}[args.stage](args.output, config)


if __name__ == "__main__":
    main()
