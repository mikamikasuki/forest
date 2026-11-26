"""Actual quadrature measurements exercise the publication presentation path."""
from copy import deepcopy
import json
import math
from pathlib import Path

import numpy as np
import pytest

from research.figures.render import render_figure
from research.figures.statistical import uncertainty_bounds
from research.paper.evidence import collect_evidence, manuscript_prompt, validate_draft
from research.paper.manuscript import check_paper, compile_paper, generate_paper
from research.paper.statistical_workflow import prepare_statistical_presentation


def measured_evidence(tmp_path, checkpoints=(16, 64, 256), *, paired=False):
    runs = []
    for seed in range(5):
        observations = []
        for dataset in ('sine', 'exponential'):
            function = np.sin if dataset == 'sine' else np.exp
            endpoint = math.pi if dataset == 'sine' else 1
            exact = 2 if dataset == 'sine' else math.e - 1
            for count in checkpoints:
                rng = np.random.default_rng(seed)
                random = rng.uniform(0, 1, count)
                samples = {'Monte Carlo': random, 'Stratified': (np.arange(count) + random) / count,
                           'Antithetic': np.concatenate([random[:count // 2], 1 - random[:count // 2]])}
                for method, points in samples.items():
                    estimate = endpoint * float(np.mean(function(points * endpoint)))
                    error = abs(estimate - exact)
                    for metric, value in [('absolute_error', error), ('squared_error', error ** 2)]:
                        observations.append({'dataset': dataset, 'method': method, 'metric': metric,
                                             'seed': seed, 'x': count, 'value': value})
        folder = tmp_path / ('run-' + str(seed))
        folder.mkdir()
        artifact = {'statistics': {'observations': observations,
            'metrics': {metric: {'direction': 'lower', 'unit': 'integral units' if metric == 'absolute_error' else 'squared integral units', 'precision': 3}
                        for metric in ('absolute_error', 'squared_error')},
            'design': {'datasets': ['sine', 'exponential'], 'methods': ['Monte Carlo', 'Stratified', 'Antithetic'],
                       'metrics': ['absolute_error', 'squared_error'], 'seeds': list(range(5)), 'x': list(checkpoints)},
            'axes': {'x': {'label': 'Function evaluations', 'unit': 'count'}},
            'uncertainty': {'type': 't_ci', 'unit': 'seed', 'independent': True, 'confidence': .95}}}
        if paired and seed == 0:
            artifact['statistics']['comparisons'] = [{'baseline': 'Monte Carlo', 'candidate': 'Stratified'}]
        (folder / 'metrics.json').write_text(json.dumps(artifact))
        runs.append({'id': 'quad-' + str(seed), 'status': 'completed', 'directory': str(folder)})
    return collect_evidence(runs)


def plot_data(evidence):
    return {'statistical_results': evidence['statistics'], 'metric_bindings': evidence['metrics']}


def authored_paper(evidence):
    blocks = [{'type': 'paragraph', 'id': 'comparisons', 'text':
               'The complete measured comparisons in ' + ', '.join('Table [[ref:' + block['label'] + ']]'
               for block in evidence['statistical_presentation']['tables']) + ' retain every evaluated quadrature method and resolution.'}]
    for block in deepcopy(evidence['statistical_presentation']['tables']):
        block['anchor'] = {'after': 'comparisons'}
        blocks.append(block)
    for index, figure in enumerate(evidence['figures']):
        label = 'fig:error-' + str(index)
        paragraph = 'curve-' + str(index)
        blocks.extend([{'type': 'paragraph', 'id': paragraph, 'text': 'Figure [[ref:' + label + ']] shows the measured error over function evaluations.'},
                       {'type': 'figure', 'figure_id': figure['id'], 'caption': figure['caption'], 'label': label,
                        'anchor': {'after': paragraph}, 'argumentative_duty': 'effectiveness', 'layout': {'span': 'page'}}])
    reference = evidence['statistics']['records'][0]['refs']['estimate']
    return {'title': 'Quadrature error over sampling budgets',
            'abstract': 'Independent seeded quadrature gives a recorded error of [[metric:' + reference + ']].',
            'sections': [{'title': 'Error comparisons', 'role': 'results', 'blocks': blocks}],
            'conclusion': 'The measured curves distinguish the methods at each evaluated budget.', 'claim_ids': []}


def test_curves_preserve_every_run_method_dataset_and_checkpoint(tmp_path):
    evidence = measured_evidence(tmp_path)
    paths = render_figure(tmp_path / 'curve', plot_data(evidence), {'metric': 'absolute_error'}, 'line')
    report = json.loads(Path(paths['report']).read_text())
    assert report['panels'] == 2 and report['displayed_points'] == 18
    assert report['unit'] == 'integral units'
    assert {row['n_units'] for row in report['displayed_measurements']} == {5}
    assert {row['x'] for row in report['displayed_measurements']} == {16, 64, 256}
    assert report['minimum_font_pt'] >= 8
    assert '95% CI' in report['uncertainty']['definitions'][0]
    for row in report['displayed_measurements']:
        assert uncertainty_bounds(row) == (row['ci_low'], row['ci_high'])
    assert Path(paths['pdf']).read_bytes().startswith(b'%PDF')
    svg = Path(paths['svg']).read_text()
    assert 'Function evaluations' in svg and '↓' in svg


def test_plot_rejects_value_identity_and_unit_tampering(tmp_path):
    evidence = measured_evidence(tmp_path)
    for field, value in [('method', 'Stratified'), ('estimate', 99)]:
        data = plot_data(deepcopy(evidence))
        original = data['statistical_results']['records'][0]
        if original.get(field) == value:
            value = 'changed-method'
        original[field] = value
        with pytest.raises(ValueError, match='binding'):
            render_figure(tmp_path / field, data, {'metric': 'absolute_error'}, 'line')
    with pytest.raises(ValueError, match='units'):
        render_figure(tmp_path / 'units', plot_data(evidence), {'metric': 'absolute_error', 'unit': '%'}, 'line')


@pytest.mark.parametrize(('collection', 'kind'), [('records', 'line'), ('comparisons', 'forest')])
@pytest.mark.parametrize(('field', 'value'), [('unit', 'seconds'), ('direction', 'higher')])
def test_plot_rejects_metadata_that_differs_from_bound_metric(tmp_path, collection, kind, field, value):
    evidence = measured_evidence(tmp_path, (16, 64), paired=kind == 'forest')
    data = plot_data(evidence)
    for row in data['statistical_results'][collection]:
        if row['metric'] == 'absolute_error':
            row[field] = value

    with pytest.raises(ValueError, match='source binding: ' + field):
        render_figure(tmp_path / 'plot', data, {'metric': 'absolute_error'}, kind)


def test_plot_rejects_missing_checkpoint_and_seed_axis(tmp_path):
    evidence = measured_evidence(tmp_path)
    data = plot_data(deepcopy(evidence))
    data['statistical_results']['records'].pop(0)
    with pytest.raises(ValueError, match='every declared method'):
        render_figure(tmp_path / 'incomplete', data, {'metric': 'absolute_error'}, 'line')
    with pytest.raises(ValueError, match='named measured x'):
        render_figure(tmp_path / 'seed-axis', plot_data(evidence), {'metric': 'absolute_error', 'xlabel': 'seed'}, 'line')


def test_reported_sd_is_descriptive_not_a_confidence_interval():
    assert uncertainty_bounds({'estimate': 3, 'sd': .2, 'uncertainty': {'type': 'reported_sd'}}) == (2.8, 3.2)


def test_display_aliases_cannot_exchange_measured_identities(tmp_path):
    evidence = measured_evidence(tmp_path)
    for aliases in ({'labels': {'Monte Carlo': 'Stratified', 'Stratified': 'Monte Carlo'}},
                    {'dataset_labels': {'sine': 'exponential', 'exponential': 'sine'}},
                    {'labels': {'Monte Carlo': 'Same', 'Stratified': 'Same'}}):
        with pytest.raises(ValueError, match='identit'):
            render_figure(tmp_path / 'aliases', plot_data(evidence), {'metric': 'absolute_error', **aliases}, 'line')


def test_full_publication_path_binds_derived_numbers_and_compiles(tmp_path):
    evidence = measured_evidence(tmp_path)
    prepared = prepare_statistical_presentation(evidence, tmp_path / 'presentation', {'columns': 'double'})
    draft = authored_paper(prepared)
    output = tmp_path / 'paper'
    generated = generate_paper(None, output, evidence=prepared, draft=draft, layout={'columns': 'double'})
    derived = [binding for binding in generated['bindings'] if binding.get('artifact') == 'statistical_results']
    assert derived and all(binding['file'] == 'evidence/statistical_results.json' for binding in derived)
    assert all(binding['source_refs'] for binding in derived)
    assert check_paper(output)['issues'] == []
    compiled = compile_paper(output)
    if compiled['status'] != 'unavailable':
        assert compiled['status'] == 'completed', compiled.get('log')
        assert (output / 'paper.pdf').is_file()
    stored = json.loads((output / 'evidence' / 'statistical_results.json').read_text())
    stored['records'][0]['method'] = 'Wrong method'
    (output / 'evidence' / 'statistical_results.json').write_text(json.dumps(stored))
    assert 'statistical_identity' in {issue['code'] for issue in check_paper(output)['issues']}


def test_publication_writer_cannot_omit_declared_tables(tmp_path):
    prepared = prepare_statistical_presentation(measured_evidence(tmp_path), tmp_path / 'presentation')
    draft = authored_paper(prepared)
    draft['sections'][0]['blocks'] = [block for block in draft['sections'][0]['blocks'] if block.get('type') != 'table']
    with pytest.raises(ValueError, match='every declared statistical'):
        validate_draft(draft, prepared)
    prompt = manuscript_prompt(prepared, 'Compare seeded quadrature')
    assert 'seeds are repetition identities' in prompt
    assert 'statistics_spec' in prompt and 'Point-estimate rank is not significance' in prompt


def test_changed_derived_source_is_rejected_before_writing(tmp_path):
    evidence = measured_evidence(tmp_path)
    prepared = prepare_statistical_presentation(evidence, tmp_path / 'presentation')
    draft = authored_paper(prepared)
    prepared['statistics']['records'][0]['estimate'] += 1
    with pytest.raises(ValueError, match='binding|changed|reference'):
        generate_paper(None, tmp_path / 'paper', evidence=prepared, draft=draft)


def test_required_table_label_cannot_mask_a_toy_comparison(tmp_path):
    prepared = prepare_statistical_presentation(measured_evidence(tmp_path), tmp_path / 'presentation')
    draft = authored_paper(prepared)
    block = next(block for block in draft['sections'][0]['blocks'] if block.get('type') == 'table')
    block['statistics_spec'].update({'require_complete': False, 'methods': ['Stratified'], 'datasets': ['sine']})
    block['argumentative_duty'] = 'mechanism'
    with pytest.raises(ValueError, match='declared scientific scope'):
        validate_draft(draft, prepared)


def test_many_facets_are_paginated_without_losing_measurements(tmp_path):
    from research.analysis.presentation import attach_statistical_evidence
    from research.paper.evidence import _numbers
    evidence = measured_evidence(tmp_path)
    # Re-label repeated *actual* measurements as a presentation stress fixture;
    # this is a layout check, not a claim of additional scientific datasets.
    runs = []
    for run in evidence['runs']:
        metrics = deepcopy(run['metrics'])
        protocol = metrics['statistics']
        original = protocol['observations']
        protocol['observations'] = [{**row, 'dataset': 'layout-panel-' + str(index)}
            for index in range(10) for row in original if row['dataset'] == 'sine']
        protocol['design']['datasets'] = ['layout-panel-' + str(index) for index in range(10)]
        runs.append({**run, 'metrics': metrics})
    metrics = [{'id': 'm' + str(index), 'run_id': run_id, 'pointer': pointer, 'value': value}
               for index, (run_id, pointer, value) in enumerate((run['id'], pointer, value)
                   for run in runs for pointer, value in _numbers(run['metrics']))]
    enlarged = attach_statistical_evidence({**evidence, 'runs': runs, 'metrics': metrics})
    prepared = prepare_statistical_presentation(enlarged, tmp_path / 'large-presentation')
    figures = prepared['figures']
    assert len(figures) == 6  # Three full facet pages for each measured metric.
    for metric in ('absolute_error', 'squared_error'):
        reports = [figure['render_metadata'] for figure in figures if figure['render_metadata']['metric'] == metric]
        assert sum(report['displayed_points'] for report in reports) == 10 * 3 * 3
        assert all(report['panels'] <= 4 and report['height_in'] <= 8 and report['minimum_font_pt'] >= 8 for report in reports)
