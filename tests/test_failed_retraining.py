"""Reproduce a failed retrain with the actual local experiment pipeline."""
import numpy as np
import pandas as pd
import pytest

from research.experiments import case


def test_evaluate_requires_successful_retraining_after_fit_failure(tmp_path, monkeypatch):
    rows = 240
    frame = pd.DataFrame({'feature': np.arange(rows) / rows, 'target': np.arange(rows) % 2})
    monkeypatch.setattr(case, '_download_dataset', lambda _name, _cache: (frame, rows))
    options = {
        'datasets': ['adult'], 'seeds': [7], 'c_grid': [0.1], 'hgb_iterations': 3,
        'bootstrap_samples': 100, 'calibration_budgets': [], 'n_threads': 1,
    }
    case.prepare(tmp_path, options)
    case.train(tmp_path)
    assert case.evaluate(tmp_path)['status'] == 'ANALYZED'
    manifest = tmp_path / 'training.json'
    probabilities = tmp_path / 'probabilities_adult_7.npz'
    recorded_manifest = manifest.read_bytes()
    recorded_probabilities = probabilities.read_bytes()
    analysis = tmp_path / 'analysis_config.json'
    original_analysis = analysis.read_bytes()

    def fail_fit(*_args, **_kwargs):
        raise RuntimeError('Injected first estimator fit failure')

    overrides = {'c_grid': [10.0]}
    with monkeypatch.context() as failing:
        failing.setattr(case.LogisticRegression, 'fit', fail_fit)
        with pytest.raises(RuntimeError, match='Injected first estimator fit failure'):
            case.train(tmp_path, overrides)
    assert manifest.read_bytes() == recorded_manifest
    assert probabilities.read_bytes() == recorded_probabilities

    with pytest.raises(ValueError, match='training attempt did not complete'):
        case.evaluate(tmp_path)
    assert analysis.read_bytes() == original_analysis

    records = case.train(tmp_path, overrides)
    assert records[0]['selected_c'] == 10.0
    assert case.evaluate(tmp_path, {'bootstrap_samples': 101})['status'] == 'ANALYZED'
    with pytest.raises(ValueError, match='training configuration changed'):
        case.evaluate(tmp_path, {'c_grid': [20.0]})

    # Previously completed protocol 2 directories have no attempt receipt.
    (tmp_path / 'training_attempt.json').unlink()
    assert case.evaluate(tmp_path)['status'] == 'ANALYZED'
