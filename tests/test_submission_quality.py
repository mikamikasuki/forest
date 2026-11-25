import json

import pytest

from research.publication import assess_submission


@pytest.mark.parametrize(('analysis_ids', 'missing_independent_analysis'), [
    (['analysis-1'], True),
    (['analysis-1', 'analysis-1'], True),
    (['analysis-1', 'analysis-2'], False),
    (['analysis-1', 'analysis-1', 'analysis-2'], False),
    (['analysis-1', 'missing-run'], True),
])
def test_submission_requires_two_distinct_completed_analysis_runs(tmp_path, analysis_ids, missing_independent_analysis):
    runs = []
    for run_id in ('analysis-1', 'analysis-2'):
        output = tmp_path / 'runs' / run_id
        output.mkdir(parents=True)
        attempt_id = f'{run_id}-attempt'
        (output / 'result.json').write_text(json.dumps({'status': 'completed', 'attempt_id': attempt_id}))
        runs.append({
            'id': run_id, 'kind': 'analysis', 'status': 'completed',
            'output_path': f'runs/{run_id}',
            'config': {'execution_attempt': {'id': attempt_id}},
            'metrics': {'observed_metrics': {'improvement': 0.1, 'ci_low': 0.01, 'ci_high': 0.2}},
        })
    manifest = {
        'analysis_run_ids': analysis_ids,
        'statistics': [{'analysis_run_id': 'analysis-1', 'effect': 0.1, 'ci_low': 0.01, 'ci_high': 0.2}],
    }

    audit = assess_submission(tmp_path, runs=runs, manifest=manifest)

    codes = {gap['code'] for gap in audit['gaps']}
    assert ('independent_analysis' in codes) == missing_independent_analysis
    assert 'statistics' not in codes
    assert 'unbound_statistics' not in codes
