"""End-to-end evidence scope and real statistical artifact preparation."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from research.analysis.presentation import attach_statistical_evidence
from research.paper.evidence import _numbers, collect_evidence
from research.paper.statistical_workflow import prepare_statistical_presentation
from research.paper.tables import (default_statistical_tables, materialize_statistical_tables,
                                  validate_statistical_tables)
import research.paper.statistical_workflow as workflow


def measured_evidence(tmp_path, *, curve=False, datasets=('D1', 'D2'), metrics=('score',),
                      paired=False, intervals=False, points=(100, 200), declared_design=True):
    runs = []
    for index, seeds in enumerate(((0, 1), (2,))):
        rows = []
        for dataset in datasets:
            for metric in metrics:
                for method, offset in (('baseline', 0), ('candidate', 4)):
                    for point in points if curve else (None,):
                        for seed in seeds:
                            rows.append({'dataset': dataset, 'method': method, 'metric': metric,
                                         'seed': seed, 'x': point,
                                         'value': 10 + seed + offset + (point / 100 if point else 0)})
        statistical = {'observations': rows,
                       'metrics': {metric: {'direction': 'higher', 'unit': 'score', 'precision': 2} for metric in metrics},
                       'axes': {'x': {'label': 'Training samples', 'unit': 'samples'}},
                       'design': {'datasets': list(datasets), 'methods': ['baseline', 'candidate'],
                                  'metrics': list(metrics), 'seeds': [0, 1, 2]}}
        if curve:
            statistical['design']['x'] = list(points)
        if not declared_design:
            del statistical['design']
        if intervals:
            statistical['uncertainty'] = {'type': 't_ci', 'unit': 'seed', 'independent': True}
        if paired:
            statistical['comparisons'] = [{'candidate': 'candidate', 'baseline': 'baseline',
                                          'uncertainty': {'type': 't_ci', 'unit': 'seed', 'independent': True}}]
        directory = tmp_path / ('run-' + str(index))
        directory.mkdir()
        original = {'statistics': statistical}
        (directory / 'metrics.json').write_text(json.dumps(original))
        runs.append({'id': 'run-' + str(index), 'status': 'completed', 'directory': str(directory),
                     'metrics_file': 'metrics.json', 'metrics': original})
    evidence = {'runs': runs, 'metrics': [], 'figures': [], 'claims': [], 'sources': []}
    for run in runs:
        for pointer, value in _numbers(run['metrics']):
            evidence['metrics'].append({'id': 'm' + str(len(evidence['metrics'])), 'run_id': run['id'],
                                        'pointer': pointer, 'value': value})
    return attach_statistical_evidence(evidence)


def test_complete_effectiveness_table_requires_declared_design(tmp_path):
    evidence = measured_evidence(tmp_path, declared_design=False)
    coverage = evidence['statistics']['coverage']
    assert coverage['complete'] is True
    assert coverage['design_declared'] is False

    plan, = default_statistical_tables(evidence)
    draft = {'sections': [{'blocks': [plan]}]}
    with pytest.raises(ValueError, match='declared experiment design'):
        materialize_statistical_tables(draft, evidence)
    with pytest.raises(ValueError, match='declared experiment design'):
        validate_statistical_tables(draft, evidence)

    plan['statistics_spec']['require_complete'] = False
    exploratory = materialize_statistical_tables(draft, evidence)
    block = exploratory['sections'][0]['blocks'][0]
    assert block['statistics_binding']['complete'] is False
    assert validate_statistical_tables(exploratory, evidence)['tables'] == 1


def report(figure):
    return json.loads((Path(figure['directory']) / figure['artifacts']['report']).read_text())


def test_complete_curves_include_all_runs_datasets_methods_and_checkpoints(tmp_path):
    evidence = measured_evidence(tmp_path, curve=True)
    original = deepcopy(evidence)
    enriched = prepare_statistical_presentation(evidence, tmp_path / 'presentation')
    assert evidence == original
    assert len(enriched['figures']) == 1
    figure = enriched['figures'][0]
    actual = report(figure)
    assert actual['kind'] == 'line'
    assert actual['panels'] == 2 and actual['plotted_rows'] == 8
    assert set(actual['selected_methods']) == {'baseline', 'candidate'}
    assert set(figure['source_run_ids']) == {'run-0', 'run-1'}
    assert 'selection' not in figure['artifacts']
    assert figure['path'].endswith('.pdf')
    assert all((Path(figure['directory']) / path).is_file() for path in figure['artifacts'].values())
    assert 'Training samples' in figure['caption']
    assert not any(path.name.startswith('.statistical-presentation-') for path in (tmp_path / 'presentation').iterdir())


def test_each_metric_receives_its_own_full_matrix_plot(tmp_path):
    evidence = measured_evidence(tmp_path, metrics=('score', 'precision'))
    result = prepare_statistical_presentation(evidence, tmp_path / 'presentation')
    assert {report(figure)['metric'] for figure in result['figures']} == {'score', 'precision'}
    assert all(report(figure)['kind'] == 'heatmap' and report(figure)['plotted_rows'] == 4 for figure in result['figures'])
    assert len(result['statistical_presentation']['figure_ids']) == 2
    assert result['statistical_presentation']['tables']
    assert 'statistical identity' not in str(result['figures'][0]['caption'])


def test_complete_paired_intervals_choose_forest_and_supported_conditional_caption(tmp_path):
    evidence = measured_evidence(tmp_path, paired=True, intervals=True)
    result = prepare_statistical_presentation(evidence, tmp_path / 'presentation')
    actual = report(result['figures'][0])
    assert actual['kind'] == 'forest' and actual['plotted_rows'] == 2
    assert 'candidate improves score over baseline on D1' in result['figures'][0]['caption']
    assert 'positive differences favor' in result['figures'][0]['caption']
    assert set(actual['selected_record_ids']) == {row['id'] for row in evidence['statistics']['comparisons']}


def test_one_dataset_without_paired_intervals_uses_bar(tmp_path):
    evidence = measured_evidence(tmp_path, datasets=('D',))
    result = prepare_statistical_presentation(evidence, tmp_path / 'presentation')
    assert report(result['figures'][0])['kind'] == 'bar'
    assert 'improves' not in result['figures'][0]['caption']


def test_one_checkpoint_is_points_not_an_invented_continuous_curve(tmp_path):
    evidence = measured_evidence(tmp_path, curve=True, points=(100,))
    result = prepare_statistical_presentation(evidence, tmp_path / 'presentation')
    assert report(result['figures'][0])['kind'] == 'scatter'
    assert 'checkpoints' in result['figures'][0]['caption']


def test_admitted_figure_records_round_trip_through_evidence_collector(tmp_path):
    evidence = measured_evidence(tmp_path, curve=True)
    result = prepare_statistical_presentation(evidence, tmp_path / 'presentation')
    admitted = collect_evidence(evidence['runs'], figures=result['figures'])
    assert len(admitted['figures']) == 1
    assert admitted['figures'][0]['id'] == result['figures'][0]['id']
    assert admitted['figures'][0]['render_metadata']['kind'] == 'line'
    assert admitted['figures'][0]['render_metadata']['evidence_role'] == 'empirical'
    assert admitted['figures'][0]['caption_context']['caption_contract'] == workflow.CAPTION_CONTRACT


def test_existing_figures_keep_their_ids_and_content(tmp_path):
    evidence = measured_evidence(tmp_path, curve=True)
    authored = {'id': 'statistical-score-line', 'caption': 'Authored figure', 'purpose': 'mechanism'}
    evidence['figures'] = [authored]
    result = prepare_statistical_presentation(evidence, tmp_path / 'presentation')
    assert result['figures'][0] == authored
    assert result['figures'][1]['id'] == 'statistical-score-line-2'
    assert evidence['figures'] == [authored]


def test_repreparation_replaces_only_its_own_generated_catalog(tmp_path):
    evidence = measured_evidence(tmp_path, datasets=('D',))
    first = prepare_statistical_presentation(evidence, tmp_path / 'presentation')
    second = prepare_statistical_presentation(first, tmp_path / 'presentation')
    assert len(second['figures']) == 1
    assert second['statistical_presentation']['figure_ids'] == first['statistical_presentation']['figure_ids']


def test_incomplete_matrix_and_changed_numeric_binding_fail_before_any_render(tmp_path, monkeypatch):
    evidence = measured_evidence(tmp_path)
    destination = tmp_path / 'presentation'
    monkeypatch.setattr(workflow, 'render_figure', lambda *args, **kwargs: pytest.fail('invalid evidence must not render'))
    evidence['statistics']['coverage']['complete'] = False
    with pytest.raises(ValueError, match='Complete'):
        prepare_statistical_presentation(evidence, destination)
    assert not destination.exists()
    evidence['statistics']['coverage']['complete'] = True
    evidence['statistics']['records'][0]['estimate'] += 1
    with pytest.raises(ValueError, match='binding'):
        prepare_statistical_presentation(evidence, destination)
    assert not destination.exists()


def test_failed_later_plot_publishes_no_partial_outputs(tmp_path, monkeypatch):
    evidence = measured_evidence(tmp_path, metrics=('score', 'precision'))
    original = deepcopy(evidence)
    native = workflow.render_figure
    calls = []
    def fail_second(output, data, style, kind):
        calls.append(style['metric'])
        if len(calls) == 2:
            raise ValueError('second render failed')
        return native(output, data, style, kind)
    monkeypatch.setattr(workflow, 'render_figure', fail_second)
    with pytest.raises(ValueError, match='second render failed'):
        prepare_statistical_presentation(evidence, tmp_path / 'presentation')
    assert evidence == original
    assert list((tmp_path / 'presentation').iterdir()) == []


def test_provider_review_uses_real_artifacts_and_removes_temporary_review_files(tmp_path, monkeypatch):
    evidence = measured_evidence(tmp_path, datasets=('D',))
    observed = []
    client = object()
    def review(actual_client, output, data, style, kind, *, context):
        assert actual_client is client
        assert 'leading journal or conference' in context['publication_style']
        assert context['actual_statistical_results']['records']
        paths = workflow.render_figure(output, data, style, kind)
        temporary_log = Path(output) / 'iterations' / 'model-call.txt'
        temporary_log.parent.mkdir()
        temporary_log.write_text('temporary review transport')
        observed.append(temporary_log)
        return paths, {'status': 'selected', 'candidate_id': 'print', 'ranking': []}
    monkeypatch.setattr(workflow, 'reviewed_render', review)
    result = prepare_statistical_presentation(evidence, tmp_path / 'presentation', client=client)
    figure = result['figures'][0]
    selection = json.loads((Path(figure['directory']) / figure['artifacts']['selection']).read_text())
    assert selection['status'] == 'selected'
    assert all(not filename.exists() for filename in observed)
    assert not list((tmp_path / 'presentation').rglob('*.txt'))


def test_unknown_result_formats_return_an_unmodified_copy_without_output(tmp_path):
    original = {'runs': [], 'metrics': [], 'figures': [{'id': 'authored'}]}
    result = prepare_statistical_presentation(original, tmp_path / 'presentation')
    assert result == original and result is not original
    assert not (tmp_path / 'presentation').exists()


def test_no_model_client_does_not_claim_or_invoke_visual_review(tmp_path, monkeypatch):
    evidence = measured_evidence(tmp_path, datasets=('D',))
    monkeypatch.setattr(workflow, 'reviewed_render', lambda *args, **kwargs: pytest.fail('no review provider configured'))
    result = prepare_statistical_presentation(evidence, tmp_path / 'presentation')
    assert all('selection' not in figure['artifacts'] for figure in result['figures'])


def test_double_column_multi_dataset_comparison_uses_page_width(tmp_path):
    evidence = measured_evidence(tmp_path)
    result = prepare_statistical_presentation(evidence, tmp_path / 'presentation', {'columns': 'double'})
    assert report(result['figures'][0])['width_in'] == 6.5
