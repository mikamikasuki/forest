"""Check that training consumes the bytes used to prepare its data splits."""
import json

import numpy as np
import pandas as pd
import pytest

from research.experiments import case


@pytest.mark.parametrize('changed_column', ['target', 'feature'])
def test_train_rejects_edited_prepared_csv(tmp_path, monkeypatch, changed_column):
    rows = 400
    frame = pd.DataFrame({'feature': np.arange(rows) / rows, 'target': np.arange(rows) % 2})
    monkeypatch.setattr(case, '_download_dataset', lambda _name, _cache: (frame, rows))
    options = {
        'datasets': ['adult'], 'seeds': [7], 'c_grid': [0.1], 'hgb_iterations': 3,
        'calibration_budgets': [], 'n_threads': 1,
    }
    case.prepare(tmp_path, options)
    csv = tmp_path / 'data_adult.csv'
    prepared_csv = csv.read_bytes()
    config = tmp_path / 'config.json'
    prepared_config = config.read_bytes()
    edited = frame.copy()
    if changed_column == 'target':
        edited['target'] = (np.arange(rows) % 5 != 0).astype(int)
    else:
        edited['feature'] += 1
    edited.to_csv(csv, index=False)

    with pytest.raises(ValueError, match='Prepared dataset adult changed.*prepare'):
        case.train(tmp_path, {'c_grid': [10.0]})

    assert not (tmp_path / 'training.json').exists()
    assert config.read_bytes() == prepared_config
    csv.write_bytes(prepared_csv)
    assert case.train(tmp_path)[0]['dataset'] == 'adult'


def test_train_requires_a_new_preparation_for_legacy_unverified_data(tmp_path, monkeypatch):
    rows = 400
    frame = pd.DataFrame({'feature': np.arange(rows) / rows, 'target': np.arange(rows) % 2})
    monkeypatch.setattr(case, '_download_dataset', lambda _name, _cache: (frame, rows))
    case.prepare(tmp_path, {'datasets': ['adult'], 'seeds': [7]})
    metadata = tmp_path / 'datasets.json'
    records = json.loads(metadata.read_text())
    records['adult'].pop('data_sha256', None)
    metadata.write_text(json.dumps(records))

    with pytest.raises(ValueError, match='Prepared dataset adult has no recorded checksum.*prepare'):
        case.train(tmp_path)
    assert not (tmp_path / 'training.json').exists()
