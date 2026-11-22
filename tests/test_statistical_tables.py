"""Real calculated values exercise statistical identity and LaTeX presentation."""
from copy import deepcopy
import json
import math
from pathlib import Path

import numpy as np
import pytest

from research.paper.evidence import write_manuscript
from research.paper.manuscript import compile_paper, tex
from research.paper.structure import render_section, validate_structure
from research.paper.tables import (default_statistical_tables, materialize_statistical_tables,
                                  validate_statistical_tables)


def calculated_evidence(tmp_path, datasets=4, conditions=(None,)):
    records, metrics = [], []
    for dataset in range(datasets):
        for condition in conditions:
            for method, offset in [('left', 0), ('midpoint', .5), ('right', 1)]:
                for metric in ('absolute_error', 'squared_error'):
                    errors = []
                    for intervals in (8 + dataset, 13 + dataset, 21 + dataset):
                        intervals *= 2 if condition == 'fine' else 1
                        points = (np.arange(intervals) + offset) * math.pi / intervals
                        error = abs(float(np.sin(points).sum()) * math.pi / intervals - 2)
                        errors.append(error if metric == 'absolute_error' else error ** 2)
                    record = {'id': 's' + str(len(records)), 'dataset': 'grid-' + str(dataset), 'method': method,
                              'metric': metric, 'direction': 'lower', 'unit': 'integral units' if metric == 'absolute_error' else 'squared integral units',
                              'condition': condition, 'x': None, 'estimate': float(np.mean(errors)),
                              'sd': float(np.std(errors, ddof=1)), 'n_units': len(errors), 'n_seeds': None,
                              'sampling_unit': 'resolution',
                              'uncertainty': {'type': 'sd', 'unit': 'resolution', 'scope': 'variation over the declared quadrature resolutions'},
                              'source_refs': [], 'refs': {}}
                    for field in ('estimate', 'sd', 'n_units'):
                        identifier = 'statm' + str(len(metrics))
                        record['refs'][field] = identifier
                        metrics.append({'id': identifier, 'value': record[field], 'run_id': 'computed',
                                        'pointer': '/records/' + str(len(records)) + '/' + field})
                    records.append(record)
    directory = tmp_path / 'computed'
    directory.mkdir()
    data = {'records': records}
    (directory / 'metrics.json').write_text(json.dumps(data))
    return {'runs': [{'id': 'computed', 'directory': str(directory), 'metrics_file': 'metrics.json', 'metrics': data}],
            'metrics': metrics, 'sources': [], 'figures': [], 'claims': [],
            'statistics': {'version': 1, 'records': records, 'comparisons': [], 'coverage': {'complete': True, 'design_declared': True}}}


def table_draft(evidence, spec=None):
    block = default_statistical_tables(evidence)[0]
    if spec:
        block['statistics_spec'].update(spec)
    return {'title': 'Measured quadrature errors', 'abstract': 'The recorded error is [[metric:statm0]].',
            'sections': [{'title': 'Results', 'role': 'results', 'blocks': [
                {'type': 'paragraph', 'id': 'quadrature', 'text': 'Table [[ref:tab:statistical-results]] compares errors at the supplied resolutions.'}, block]}],
            'conclusion': 'The comparison preserves the measured errors.', 'claim_ids': []}


def table(draft):
    return draft['sections'][0]['blocks'][1]


def test_computed_matrix_has_grouped_metrics_actual_units_and_sample_bindings(tmp_path):
    evidence = calculated_evidence(tmp_path)
    original = table_draft(evidence)
    draft = materialize_statistical_tables(original, evidence)
    block = table(draft)
    assert 'rows' not in table(original)
    assert len(block['rows']) == 3 and len(block['columns']) == 9
    assert len(block['column_groups']) == 4
    assert len(block['column_panels']) >= 2
    assert sorted(index for panel in block['column_panels'] for index in panel[1:]) == list(range(1, 9))
    assert all('↓' in label for label in block['columns'][1:])
    assert all('[[metric:' in cell for row in block['rows'] for cell in row[1:])
    assert 'standard deviation across resolution' in '\n'.join(block['notes'])
    assert 'training seeds' not in '\n'.join(block['notes'])
    assert 'resolution units' in '\n'.join(block['notes'])
    assert any('[[metric:statm2]]' in note for note in block['notes'])
    assert validate_statistical_tables(draft, evidence)['tables'] == 1
    assert materialize_statistical_tables(draft, evidence) == draft
    assert len(block['statistics_binding']['cells']) == 24
    assert block['cell_styles']


@pytest.mark.parametrize('tamper', ['method', 'dataset', 'cell', 'uncertainty', 'binding', 'panels', 'style'])
def test_statistical_identity_and_uncertainty_cannot_be_changed(tmp_path, tamper):
    evidence = calculated_evidence(tmp_path)
    draft = materialize_statistical_tables(table_draft(evidence), evidence)
    block = table(draft)
    if tamper == 'method':
        block['rows'][0][0], block['rows'][1][0] = block['rows'][1][0], block['rows'][0][0]
    elif tamper == 'dataset':
        block['column_groups'][0]['label'] = 'different experiment'
    elif tamper == 'cell':
        block['rows'][0][1], block['rows'][1][1] = block['rows'][1][1], block['rows'][0][1]
    elif tamper == 'uncertainty':
        block['notes'] = ['Brackets give confidence intervals.']
    elif tamper == 'binding':
        block['statistics_binding']['cells'][0]['identity']['method'] = 'different method'
    elif tamper == 'panels':
        block['column_panels'][1].pop()
    elif tamper == 'style':
        block['cell_styles']['0:0'] = 'bold'
    with pytest.raises(ValueError, match='Statistical table'):
        validate_statistical_tables(draft, evidence)
    with pytest.raises(ValueError, match='Statistical table'):
        materialize_statistical_tables(draft, evidence)


def test_main_comparison_cannot_suppress_methods_or_conditions(tmp_path):
    evidence = calculated_evidence(tmp_path, conditions=('coarse', 'fine'))
    for spec in ({'methods': ['midpoint']}, {'datasets': ['grid-0']}, {'conditions': ['fine']}):
        with pytest.raises(ValueError, match='cannot omit'):
            materialize_statistical_tables(table_draft(evidence, spec), evidence)
    draft = materialize_statistical_tables(table_draft(evidence), evidence)
    assert [group['label'] for group in table(draft)['row_groups']] == ['coarse', 'fine']
    assert len(table(draft)['rows']) == 6
    broken = deepcopy(evidence)
    broken['statistics']['records'].pop()
    with pytest.raises(ValueError, match='missing method comparison'):
        materialize_statistical_tables(table_draft(broken), broken)


def test_uncomputed_intervals_and_mislabelled_caption_are_rejected(tmp_path):
    evidence = calculated_evidence(tmp_path)
    with pytest.raises(ValueError, match='uncomputed confidence'):
        materialize_statistical_tables(table_draft(evidence, {'uncertainty': 'ci'}), evidence)
    draft = table_draft(evidence)
    table(draft)['caption'] = 'Confidence intervals of the errors.'
    with pytest.raises(ValueError, match='mislabels'):
        materialize_statistical_tables(draft, evidence)
    draft = table_draft(evidence)
    table(draft)['notes'] = ['The table uses n = 100 samples.']
    with pytest.raises(ValueError, match='must bind measured counts'):
        materialize_statistical_tables(draft, evidence)
    evidence['metrics'][0]['value'] += 1
    with pytest.raises(ValueError, match='does not match'):
        materialize_statistical_tables(table_draft(evidence), evidence)


def test_sample_size_notes_participate_in_evidence_validation_and_macro_generation(tmp_path):
    evidence = calculated_evidence(tmp_path, datasets=2)
    draft = materialize_statistical_tables(table_draft(evidence), evidence)
    texts, _ = validate_structure(draft, evidence)
    assert any('[[metric:statm2]]' in text for text in texts)
    generated = write_manuscript(tmp_path / 'paper', evidence, draft)
    assert 'statm2' in generated['evidence_report']['referenced_metrics']
    assert 'statm2' in {binding['evidence_id'] for binding in generated['bindings']}


def test_horizontal_and_vertical_panels_repeat_headers_notes_and_group_identity(tmp_path):
    evidence = calculated_evidence(tmp_path, conditions=('coarse', 'fine'))
    draft = materialize_statistical_tables(table_draft(evidence), evidence)
    plan = {'section-0-block-1': {'span': 'page', 'placement': 'auto', 'font_pt': 9,
                                 'strategy': 'split', 'max_rows': 4, 'panel_columns': 2}}
    source = render_section(draft['sections'][0], tex, tex, {}, plan, 'double')
    part_count = len(table(draft)['column_panels']) * 2
    assert source.count(r'\begin{table*}') == part_count
    assert source.count(r'\textbf{Notes.}') == part_count
    assert source.count(r'\label{tab:statistical-results}') == 1
    assert source.count(r'\addtocounter{table}{-1}') == part_count - 1
    assert r'\cmidrule(lr)' in source and r'\ensuremath{\downarrow}' in source
    assert 'fine (continued)' in source
    assert r'\resizebox' not in source and r'\scriptsize' not in source
    assert source.count(tex(table(draft)['rows'][0][1])) == 1


@pytest.mark.parametrize('columns', ['single', 'double'])
def test_real_latex_compilation_of_readable_statistical_tables(tmp_path, columns):
    evidence = calculated_evidence(tmp_path, conditions=('coarse', 'fine'))
    draft = materialize_statistical_tables(table_draft(evidence), evidence)
    generated = write_manuscript(tmp_path / 'paper', evidence, draft,
                                 layout={'columns': columns, 'max_table_rows': 4})
    source = (tmp_path / 'paper' / 'paper.tex').read_text()
    assert source.count(r'\textbf{Notes.}') >= 2
    assert r'\multicolumn{2}{c}{grid-0}' in source
    assert r'\ensuremath{\downarrow}' in source
    compiled = compile_paper(tmp_path / 'paper')
    if compiled['status'] == 'unavailable':
        pytest.skip('Actual TeX compiler required')
    assert compiled['status'] == 'completed', compiled['log']
    assert compiled['preflight']['checks']['glyphs'] == 'passed'
    assert compiled['preflight']['checks']['bindings'] == 'passed'
    assert generated['layout_plan']['blocks'][0]['font_pt'] == 9


def test_ordinary_tables_preserve_legacy_shape_and_validate_presentation(tmp_path):
    evidence = calculated_evidence(tmp_path, datasets=1)
    draft = table_draft(evidence)
    draft['sections'][0]['blocks'][1] = {'type': 'table', 'columns': ['Name', 'Value'],
                                       'rows': [['Actual error', '[[metric:statm0]]']], 'caption': 'Measured error.'}
    assert materialize_statistical_tables(draft, evidence) == draft
    assert validate_statistical_tables(draft, evidence)['tables'] == 0
    validate_structure(draft, evidence)
    table(draft)['column_panels'] = [[0, 1], [0, 1]]
    with pytest.raises(ValueError, match='exactly once'):
        validate_structure(draft, evidence)


def test_paired_comparison_table_retains_bound_effects_intervals_and_family(tmp_path):
    evidence = calculated_evidence(tmp_path, datasets=1)
    record = {'id': 'c0', 'dataset': 'grid-0', 'metric': 'absolute_error', 'condition': None, 'x': None,
              'baseline': 'left', 'candidate': 'midpoint', 'direction': 'lower',
              'improvement': evidence['statistics']['records'][0]['estimate'] - evidence['statistics']['records'][2]['estimate'],
              'ci_low': .001, 'ci_high': .04, 'n_pairs': 3, 'adjusted_p': .04,
              'confidence': .95, 'uncertainty': {'type': 'bootstrap', 'unit': 'resolution', 'confidence': .95},
              'multiplicity': {'method': 'Holm', 'family': 'declared quadrature contrasts'}, 'refs': {}}
    # This exercises the presentation boundary; inference itself is tested by
    # the statistical engine. All displayed values bind this supplied record.
    for field in ('improvement', 'ci_low', 'ci_high', 'n_pairs', 'adjusted_p'):
        reference = 'comparison' + field
        record['refs'][field] = reference
        evidence['metrics'].append({'id': reference, 'value': record[field]})
    evidence['statistics']['comparisons'] = [record]
    draft = table_draft(evidence, {'kind': 'comparisons'})
    table(draft)['argumentative_duty'] = 'mechanism'
    materialized = materialize_statistical_tables(draft, evidence)
    block = table(materialized)
    assert block['rows'][0][0] == 'midpoint vs left'
    assert '[[metric:comparisonimprovement]]' in block['rows'][0]
    assert '95% confidence intervals across paired resolution units' in '\n'.join(block['notes'])
    assert 'Holm correction' in '\n'.join(block['notes'])
    validate_statistical_tables(materialized, evidence)


def test_checkpoint_table_plan_preserves_every_measured_setting(tmp_path):
    evidence = calculated_evidence(tmp_path, datasets=2)
    records = evidence['statistics']['records']
    for index, record in enumerate(records):
        record['x'] = 10 if record['dataset'] == 'grid-0' else 20
    blocks = default_statistical_tables(evidence)
    assert [block['label'] for block in blocks] == ['tab:statistical-checkpoint-0', 'tab:statistical-checkpoint-1']
    assert [block['statistics_spec']['x'] for block in blocks] == [[10], [20]]
    selected = []
    for checkpoint, planned in zip((10, 20), blocks):
        draft = table_draft({**evidence, 'statistics': {**evidence['statistics'], 'records': [{**record, 'x': None} for record in records]}})
        draft['sections'][0]['blocks'][1] = planned
        generated = materialize_statistical_tables(draft, evidence)
        block = table(generated)
        assert any('Checkpoint = ' + str(checkpoint) in note for note in block['notes'])
        selected.extend(block['statistics_binding']['record_ids'])
        validate_statistical_tables(generated, evidence)
    assert selected == [record['id'] for record in records]


def test_public_sampling_notes_keep_one_count_and_scientific_scope(tmp_path):
    evidence = calculated_evidence(tmp_path, datasets=1)
    for index, record in enumerate(evidence['statistics']['records']):
        record['n_seeds'] = record['n_units']
        record['sampling_unit'] = 'seed'
        record['uncertainty'].update(unit='seed', independent=True,
            scope='Across supplied seeds, conditional on supplied datasets and measured objects')
        reference = 'seedcount' + str(index)
        record['refs']['n_seeds'] = reference
        evidence['metrics'].append({'id': reference, 'value': record['n_seeds']})
    draft = materialize_statistical_tables(table_draft(evidence), evidence)
    notes = '\n'.join(table(draft)['notes'])
    assert 'standard deviation across independent seeded runs; on the evaluated data' in notes
    assert 'n = [[metric:statm2]] independent seeded runs' in notes
    assert 'seedcount' not in notes and 'training seeds' not in notes
    assert 'supplied' not in notes
    # An explicit scientific target remains the author's actual definition.
    for record in evidence['statistics']['records']:
        record['uncertainty']['scope'] = 'Conditional on matched patient cohorts'
    notes = '\n'.join(table(materialize_statistical_tables(table_draft(evidence), evidence))['notes'])
    assert 'Conditional on matched patient cohorts' in notes


def test_checkpoint_caption_and_note_bind_named_axis_value(tmp_path):
    evidence = calculated_evidence(tmp_path, datasets=1)
    evidence['statistics']['axes'] = {'x': {'name': 'training_size', 'label': 'Training data', 'unit': 'examples'}}
    for index, record in enumerate(evidence['statistics']['records']):
        record['x'] = 16
        reference = 'checkpoint' + str(index)
        record['refs']['x'] = reference
        evidence['metrics'].append({'id': reference, 'value': 16})
    planned = default_statistical_tables(evidence)[0]
    assert planned['caption'] == 'Method comparisons at Training data = [[metric:checkpoint0]] examples.'
    assert 'scope' not in planned['statistics_spec']
    draft = {'sections': [{'title': 'Results', 'blocks': [planned]}]}
    block = materialize_statistical_tables(draft, evidence)['sections'][0]['blocks'][0]
    assert block['notes'][0] == 'Training data = [[metric:checkpoint0]] examples.'
    assert not any('setting' in note or 'supplied' in note for note in block['notes'])
