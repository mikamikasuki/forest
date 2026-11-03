"""Actual arithmetic renders, artifact admission and argumentative placement.

Provider-backed selection is exercised by the live provider qualification. These
unit regressions never invent successful model calls or reviewer receipts.
"""
from copy import deepcopy
import json
import math
from pathlib import Path
import time

import numpy as np
import pytest

from research.figures.render import render_figure
from research.figures.model_workflow import review_with_models, reviewed_render
from research.figures.workflow import REVIEW_ROLES, render_candidates, register_candidates, review_requests, select_candidate
from research.paper.evidence import collect_evidence, write_manuscript
from research.paper.manuscript import check_paper, compile_paper
from research.paper.structure import section_blocks


def arithmetic_rows():
    implementations = {'sum': sum, 'fsum': math.fsum, 'numpy': np.sum}
    return [{'dataset': 'n=' + str(n), 'method': name, 'score': float(implementation(range(n)))}
            for n in range(10, 130, 10) for name, implementation in implementations.items()]


def material(tmp_path):
    run = tmp_path / 'experiment'
    run.mkdir()
    values = [sum(range(n)) for n in range(10, 130, 10)]
    (run / 'metrics.json').write_text(json.dumps({'integer_totals': values}))
    artifacts = tmp_path / 'studio' / 'figure'
    outputs = render_figure(artifacts, arithmetic_rows(), {'metric': 'score', 'unit': 'integer total', 'height': 4.5}, 'heatmap')
    records = [{'id': 'comparison', 'run_id': 'sum-run', 'source_run_ids': ['sum-run'],
        'directory': str(artifacts), 'path': 'figure.pdf', 'caption': 'Observed integer totals.',
        'artifacts': {key: Path(outputs[key]).name for key in ('source', 'data', 'style', 'report')}}]
    evidence = collect_evidence([{'id': 'sum-run', 'status': 'completed', 'directory': str(run)}], figures=records)
    draft = {'title': 'Arithmetic visual placement verification', 'abstract': 'The first recorded total is [[metric:m0]].',
        'sections': [{'title': 'Results', 'role': 'results', 'paragraphs': [
            'We execute three independent standard-library arithmetic implementations.',
            'Table [[ref:tab:totals]] preserves the recorded totals; Figure [[ref:fig:totals]] compares all observed arithmetic implementations.',
            'The final paragraph discusses the arithmetic equality of the implementations.'], 'blocks': [
                {'type': 'table', 'label': 'tab:totals', 'columns': ['Sequence length', 'Integer total'],
                 'rows': [[str(n), f'[[metric:m{i}]]'] for i, n in enumerate(range(10, 130, 10))],
                 'caption': 'Every recorded integer total.'},
                {'type': 'figure', 'figure_id': 'comparison', 'label': 'fig:totals', 'caption': 'All supplied implementations and sequence lengths.'}]}],
        'conclusion': 'The source bundle preserves the actual integer calculations.', 'claim_ids': []}
    return evidence, draft, records


def test_studio_artifacts_reach_manuscript_and_remain_standalone(tmp_path):
    evidence, draft, _ = material(tmp_path)
    output = tmp_path / 'paper'
    generated = write_manuscript(output, evidence, draft)
    binding = generated['figures'][0]
    assert binding['figure_id'] == 'comparison'
    assert binding['source_run_ids'] == ['sum-run']
    assert set(binding['artifacts']) == {'source', 'data', 'style', 'report'}
    assert all((output / filename).is_file() for filename in binding['artifacts'].values())
    assert json.loads((output / binding['artifacts']['data']).read_text()) == arithmetic_rows()
    assert evidence['figures'][0]['render_metadata']['input_rows'] == 36
    assert evidence['figures'][0]['render_metadata']['evidence_density']['datasets'] == 12
    assert check_paper(output)['issues'] == []
    (output / binding['artifacts']['data']).unlink()
    assert any(issue['code'] == 'missing_figure_companion' for issue in check_paper(output)['issues'])


def test_caption_companion_reaches_writer_evidence_and_saved_manuscript(tmp_path):
    _, draft, records = material(tmp_path)
    companion = {'caption_text': 'Observed totals from three actual arithmetic implementations.',
                 'run_aliases': {'R1': 'sum-run'}, 'display_rounding': 'Exact integers.'}
    folder = Path(records[0]['directory'])
    (folder / 'figure_caption_context.json').write_text(json.dumps(companion))
    records[0]['artifacts']['caption_context'] = 'figure_caption_context.json'
    evidence = collect_evidence([{'id': 'sum-run', 'status': 'completed',
        'directory': str(tmp_path / 'experiment')}], figures=records)
    assert evidence['figures'][0]['caption_context'] == companion
    output = tmp_path / 'paper'
    generated = write_manuscript(output, evidence, draft)
    saved = output / generated['figures'][0]['artifacts']['caption_context']
    assert json.loads(saved.read_text()) == companion
    assert check_paper(output)['issues'] == []


def test_legacy_visuals_follow_interpretation_and_compiled_page_anchor(tmp_path):
    evidence, draft, _ = material(tmp_path)
    output = tmp_path / 'paper'
    generated = write_manuscript(output, evidence, draft)
    source = (output / 'paper.tex').read_text()
    interpretation = source.index('Table \\ref{tab:totals}')
    table = source.index('\\begin{table}')
    figure = source.index('\\begin{figure}')
    following = source.index('The final paragraph')
    assert interpretation < table < figure < following
    assert '\\usepackage{flafter}' in source
    assert source.count('\\FloatBarrier') >= 2
    placements = json.loads((output / 'placement_report.json').read_text())['placements']
    assert [item['after'] for item in placements] == ['paragraph-1', 'paragraph-1']
    assert all(item['anchor_source'] == 'first_reference' for item in placements)
    assert generated['layout_plan']['warnings'] == []
    compiled = compile_paper(output)
    if compiled['status'] == 'unavailable':
        pytest.skip('Actual TeX compiler required for PDF placement regression')
    assert compiled['status'] == 'completed', compiled['log']
    assert compiled['preflight']['checks']['visual_placement'] == 'passed'
    assert all(item['page_distance'] in (0, 1) for item in compiled['preflight']['visual_placements'])


def test_explicit_paragraph_anchor_is_authoritative_and_cannot_be_invented():
    table = {'type': 'table', 'label': 'tab:comparison', 'anchor': {'after': 'interpretation'},
             'columns': ['Value'], 'rows': [['[[metric:m0]]']], 'caption': 'Observed result.'}
    section = {'title': 'Results', 'blocks': [
        {'type': 'paragraph', 'id': 'preview', 'text': 'Table [[ref:tab:comparison]] previews the comparison.'},
        table,
        {'type': 'paragraph', 'id': 'interpretation', 'text': 'The relevant measured interpretation.'},
        {'type': 'paragraph', 'id': 'next', 'text': 'The subsequent argument.'}]}
    assert [block.get('id', block.get('label')) for block in section_blocks(section)] == ['preview', 'interpretation', 'tab:comparison', 'next']
    missing = deepcopy(section)
    missing['blocks'][1]['anchor']['after'] = 'not-in-the-draft'
    with pytest.raises(ValueError, match='actual paragraph'):
        section_blocks(missing)
    repeated = deepcopy(section)
    repeated['blocks'][3]['id'] = 'interpretation'
    with pytest.raises(ValueError, match='unique'):
        section_blocks(repeated)


def test_plural_plain_references_place_every_visual_after_the_argument():
    visuals = [{'type': 'table', 'label': label, 'columns': ['Value'], 'rows': [['[[metric:m0]]']], 'caption': 'Observed.'}
               for label in ('tab:first', 'tab:second', 'tab:third')]
    section = {'title': 'Results', 'paragraphs': ['Tables tab:first, tab:second, and tab:third jointly establish the comparison.',
                'The subsequent argument.'], 'blocks': visuals}
    blocks = section_blocks(section)
    assert [item.get('label') for item in blocks[1:4]] == ['tab:first', 'tab:second', 'tab:third']
    assert blocks[-1]['text'] == 'The subsequent argument.'


def test_external_and_manifest_figure_companions_stay_inside_their_directory(tmp_path):
    evidence, _, records = material(tmp_path)
    runs = [{'id': 'sum-run', 'status': 'completed', 'directory': evidence['runs'][0]['directory']}]
    outside = tmp_path / 'outside.json'
    outside.write_text('{}')
    invalid = deepcopy(records)
    invalid[0]['artifacts']['data'] = '../../outside.json'
    with pytest.raises(ValueError, match='admitted artifact directory'):
        collect_evidence(runs, figures=invalid)
    invalid = deepcopy(records)
    invalid[0]['source_run_ids'] = ['not-in-the-evidence']
    with pytest.raises(ValueError, match='supplied completed evidence runs'):
        collect_evidence(runs, figures=invalid)
    for unbound in (
        {k: v for k, v in deepcopy(records[0]).items() if k not in ('run_id', 'source_run_ids')},
        {**deepcopy(records[0]), 'run_id': None, 'source_run_ids': []},
        {**deepcopy(records[0]), 'run_id': '', 'source_run_ids': ['sum-run']},
        {**deepcopy(records[0]), 'source_run_ids': []},
        {**deepcopy(records[0]), 'source_run_ids': None},
    ):
        with pytest.raises(ValueError, match='supplied (?:evidence run|completed evidence runs)'):
            collect_evidence(runs, figures=[unbound])
    run = Path(runs[0]['directory'])
    (run / 'figures.json').write_text(json.dumps([{'id': 'escape', 'directory': records[0]['directory'], 'path': '../studio/figure/figure.pdf'}]))
    with pytest.raises(ValueError, match='inside its evidence run'):
        collect_evidence(runs)


def test_design_candidates_preserve_actual_data_and_need_actual_model_receipts(tmp_path):
    rows = arithmetic_rows()
    bundle = render_candidates(tmp_path, rows, {'metric': 'score', 'unit': 'integer total', 'height': 4.5}, 'heatmap')
    assert len(bundle['candidates']) == 3
    for candidate in bundle['candidates']:
        assert Path(candidate['outputs']['pdf']).read_bytes().startswith(b'%PDF')
        assert json.loads(Path(candidate['outputs']['data']).read_text()) == rows
        assert candidate['report']['matrix_shape'] == [12, 3]
        assert candidate['report']['evidence_density']['observations'] == 36
    jobs = review_requests(bundle, {'purpose': 'Compare the actual arithmetic implementations.'})
    assert [job['role'] for job in jobs] == ['Evidence Reviewer', 'Figure Critic', 'Visual Editor']
    assert json.loads(jobs[0]['prompt'])['measurement_data'] == rows
    with pytest.raises(ValueError, match='all independent review roles'):
        select_candidate(bundle, [])
    with pytest.raises(ValueError, match='actual completed provider receipt'):
        select_candidate(bundle, [{'role': jobs[0]['role'], 'reviews': []}])
    with pytest.raises(ValueError, match='presentation only'):
        render_candidates(tmp_path / 'altered', rows, {'metric': 'score'}, candidates=[
            {'id': 'omit', 'style': {'methods': ['sum']}}, {'id': 'all', 'style': {}}])


def test_external_images_register_only_real_job_artifacts(tmp_path):
    source = render_figure(tmp_path / 'actual-chart', arithmetic_rows(), {'metric': 'score', 'unit': 'integer total'}, 'heatmap')
    bundle = register_candidates(tmp_path, [{'id': 'first', 'outputs': {'png': source['png']}},
        {'id': 'second', 'outputs': {'png': source['png']}}])
    assert all(Path(candidate['outputs']['png']).is_file() for candidate in bundle['candidates'])
    assert all(candidate['report']['evidence_role'] == 'conceptual_illustration' for candidate in bundle['candidates'])
    with pytest.raises(ValueError, match='actual files inside'):
        register_candidates(tmp_path / 'new-job', [{'id': 'first', 'outputs': {'png': source['png']}},
            {'id': 'second', 'outputs': {'png': source['png']}}])


def test_dense_comparison_refuses_ambiguous_or_incomplete_matrix(tmp_path):
    rows = arithmetic_rows()
    with pytest.raises(ValueError, match='measurement for every'):
        render_figure(tmp_path / 'missing', rows[:-1], {'metric': 'score'}, 'heatmap')
    with pytest.raises(ValueError, match='explicitly aggregated'):
        render_figure(tmp_path / 'duplicate', rows + [rows[0]], {'metric': 'score'}, 'heatmap')
    with pytest.raises(ValueError, match='statistical unit'):
        render_figure(tmp_path / 'undeclared', rows, {'metric': 'score'}, 'forest')
    with pytest.raises(ValueError, match='without clipping'):
        render_figure(tmp_path / 'clipped', rows, {'metric': 'score', 'vmin': 50, 'vmax': 100}, 'heatmap')
    rendered = render_figure(tmp_path / 'explicit', rows, {'metric': 'score', 'unit': 'integer total',
        'vmin': 0, 'vmax': 8000, 'annotate': True, 'annotation_format': '.4g', 'rotation': 0}, 'heatmap')
    report = json.loads(Path(rendered['report']).read_text())
    assert report['color_mapping']['limits_source'] == 'explicit'
    assert report['color_mapping']['annotated_values']
    assert report['displayed_points'] == 36


class FigureReviewResponseClient:
    """Contract fixture for response-shape behavior; it does not claim live-model validation."""
    api = 'responses'
    config = {'max_output_tokens': 2048}

    def __init__(self, mode='accept', repair=None):
        self.mode = mode
        self.repair = repair or {'style': {'font_size': 10}}
        self.calls = 0
        self.review_calls = 0

    def complete(self, messages):
        self.calls += 1
        instruction = messages[0]['content']
        role = next((name for name in REVIEW_ROLES if 'Act as the independent ' + name in instruction), None)
        if role:
            review_batch = self.review_calls // len(REVIEW_ROLES)
            self.review_calls += 1
            content = messages[1]['content']
            text = next(item['text'] for item in content if item.get('type') == 'input_text')
            payload, _ = json.JSONDecoder().raw_decode(text)
            verdict = ('revise' if self.mode in ('reject', 'invalid_repair')
                       or self.mode == 'repair_once' and review_batch == 0 else 'accept')
            reasons = ['The rendered labels are too small.']
            if self.mode == 'alias':
                reasons = 'The rendered chart preserves the supplied observations.'
            if self.mode == 'malformed_review':
                reasons = {'detail': 'not a string array'}
            response_role = 'independent_' + role.lower().replace(' ', '_') if self.mode == 'alias' else role
            response = {
                'role': response_role,
                'placement': None,
                'reviews': [{
                    'candidate_id': candidate['id'],
                    'verdict': verdict,
                    'scores': {dimension: (3 if verdict == 'revise' else 5)
                               for dimension in REVIEW_ROLES[role]},
                    'reasons': reasons,
                } for candidate in payload['candidates']],
            }
            return {'text': json.dumps(response), 'model': 'contract-fixture',
                    'request_id': 'contract-request', 'usage': {'input_tokens': 1, 'output_tokens': 1}}
        if 'Repair only presentation.' in instruction:
            return {'text': json.dumps(self.repair), 'model': 'contract-fixture',
                    'request_id': 'repair-request', 'usage': {'input_tokens': 1, 'output_tokens': 1}}
        raise AssertionError('Unexpected figure model request')


def test_review_response_normalizes_known_role_alias_and_single_reason(tmp_path):
    rows = arithmetic_rows()
    bundle = render_candidates(tmp_path / 'candidates', rows,
        {'metric': 'score', 'unit': 'integer total', 'height': 4.5}, 'heatmap')
    client = FigureReviewResponseClient(mode='alias')
    reviews = review_with_models(client, bundle, tmp_path / 'reviews')
    assert [review['role'] for review in reviews] == list(REVIEW_ROLES)
    assert all(isinstance(entry['reasons'], list) and len(entry['reasons']) == 1
               for review in reviews for entry in review['reviews'])
    assert all(review['_response_normalizations'] for review in reviews)
    assert select_candidate(bundle, reviews)['status'] == 'selected'
    assert client.calls == 3


def test_malformed_review_stops_with_schema_error_without_style_repair(tmp_path):
    rows = arithmetic_rows()
    client = FigureReviewResponseClient(mode='malformed_review')
    with pytest.raises(ValueError, match='response-format error.*reasons must be a nonempty array'):
        reviewed_render(client, tmp_path / 'figure', rows,
            {'metric': 'score', 'unit': 'integer total', 'height': 4.5},
            kind='heatmap', attempts=2)
    first = tmp_path / 'figure' / 'iterations' / '1'
    assert (first / 'candidate_manifest.json').is_file()
    assert all((first / 'candidates' / name / 'figure.png').is_file()
               for name in ('compact', 'spacious', 'print'))
    assert not (tmp_path / 'figure' / 'iterations' / '2').exists()
    assert client.calls == 1


def test_invalid_repair_labels_are_rejected_before_rerender(tmp_path):
    rows = arithmetic_rows()
    client = FigureReviewResponseClient(mode='invalid_repair',
        repair={'style': {'labels': ['not', 'an', 'identity-map']}})
    with pytest.raises(ValueError, match='response-format error.*labels must map identity strings'):
        reviewed_render(client, tmp_path / 'figure', rows,
            {'metric': 'score', 'unit': 'integer total', 'height': 4.5},
            kind='heatmap', attempts=2)
    first = tmp_path / 'figure' / 'iterations' / '1'
    assert (first / 'repair_response.json').is_file()
    assert all((first / 'candidates' / name / 'figure.png').is_file()
               for name in ('compact', 'spacious', 'print'))
    assert not (tmp_path / 'figure' / 'iterations' / '2').exists()
    assert client.calls == 4


@pytest.mark.parametrize(("repair", "message"), [
    ({"style": {"paper_layout": {"columns": "invalid"}}}, "Layout columns must be single or double"),
    ({"style": {"paper_layout": "double"}}, "paper_layout must be an object"),
    ({"style": {"font_size": 7}}, "fonts of at least 8 pt"),
    ({"style": {"palette": ["not-a-renderer-color"]}}, "palette must contain colors accepted by the renderer"),
    ({"style": {"color": "not-a-renderer-color"}}, "color must be a valid renderer color"),
    ({"style": {"span": "poster"}}, "span must be column or page"),
    ({"style": {"labels": {"unobserved": "Invented identity"}}}, "keys must match identities in the supplied data"),
])
def test_domain_invalid_style_repairs_are_rejected_before_rerender(tmp_path, repair, message):
    rows = arithmetic_rows()
    client = FigureReviewResponseClient(mode='invalid_repair', repair=repair)
    with pytest.raises(ValueError, match='response-format error.*' + message):
        reviewed_render(client, tmp_path / 'figure', rows,
            {'metric': 'score', 'unit': 'integer total', 'height': 4.5},
            kind='heatmap', attempts=2)
    first = tmp_path / 'figure' / 'iterations' / '1'
    assert (first / 'repair_response.json').is_file()
    assert all((first / 'candidates' / name / 'figure.png').is_file()
               for name in ('compact', 'spacious', 'print'))
    assert not (tmp_path / 'figure' / 'iterations' / '2').exists()
    assert client.calls == 4


def test_style_repairs_validate_statistical_result_identities():
    from research.figures.model_workflow import _validate_style_repair

    data = {
        'statistical_results': {'records': [{'method': 'observed-method', 'dataset': 'observed-set'}]},
        'metric_bindings': [{'statistical_identity': {
            'method': 'observed-method', 'dataset': 'observed-set',
        }}],
    }
    with pytest.raises(ValueError, match='response-format error.*keys must match identities'):
        _validate_style_repair(
            {'style': {'labels': {'invented-method': 'Invented Method'}}},
            current_style={}, data=data, kind='bar',
        )


def test_valid_style_repair_rerenders_without_changing_observations(tmp_path):
    rows = arithmetic_rows()
    client = FigureReviewResponseClient(mode='repair_once',
        repair={'style': {'labels': {'sum': 'Summation'}}})
    outputs, selection = reviewed_render(client, tmp_path / 'figure', rows,
        {'metric': 'score', 'unit': 'integer total', 'height': 4.5},
        kind='heatmap', attempts=2)
    assert selection['status'] == 'selected'
    assert Path(outputs['pdf']).is_file() and Path(outputs['png']).is_file()
    assert json.loads(Path(outputs['data']).read_text()) == rows
    assert client.calls == 7


def test_valid_reviews_still_publish_a_figure_and_preserve_observations(tmp_path):
    rows = arithmetic_rows()
    client = FigureReviewResponseClient(mode='accept')
    outputs, selection = reviewed_render(client, tmp_path / 'figure', rows,
        {'metric': 'score', 'unit': 'integer total', 'height': 4.5},
        kind='heatmap', attempts=1)
    assert selection['status'] == 'selected'
    assert Path(outputs['pdf']).is_file() and Path(outputs['png']).is_file()
    assert json.loads(Path(outputs['data']).read_text()) == rows
    assert client.calls == 3


def test_observed_runtime_intervals_and_scatter_export_exact_measurements(tmp_path):
    from scipy.stats import t
    observations, intervals = [], []
    for n in (100, 400):
        values = list(range(n))
        for name, operation in (('sum', sum), ('fsum', math.fsum)):
            durations = []
            for repeat in range(5):
                start = time.perf_counter()
                for _ in range(100):
                    operation(values)
                duration = time.perf_counter() - start
                durations.append(duration)
                observations.append({'dataset': str(n), 'method': name, 'n': n, 'repeat': repeat, 'seconds': duration})
            estimate = float(np.mean(durations))
            half_width = float(t.ppf(.975, len(durations)-1) * np.std(durations, ddof=1) / math.sqrt(len(durations)))
            intervals.append({'dataset': str(n), 'method': name, 'seconds': estimate,
                              'lower': estimate-half_width, 'upper': estimate+half_width})
    forest = render_figure(tmp_path / 'intervals', intervals, {'metric': 'seconds', 'unit': 'seconds',
        'error': {'type': 'ci95', 'unit': 'timed execution batch', 'lower': 'lower', 'upper': 'upper'}}, 'forest')
    scatter = render_figure(tmp_path / 'observations', observations, {'metric': 'seconds', 'unit': 'seconds', 'x': 'n'}, 'scatter')
    assert Path(forest['pdf']).read_bytes().startswith(b'%PDF')
    assert json.loads(Path(forest['data']).read_text()) == intervals
    assert json.loads(Path(scatter['data']).read_text()) == observations
    assert json.loads(Path(forest['report']).read_text())['uncertainty']['unit'] == 'timed execution batch'
    assert json.loads(Path(scatter['report']).read_text())['displayed_points'] == 20
