"""Saved manuscript revisions preserve evidence and the original response."""

from copy import deepcopy
import json
import math

import pytest

from research.paper.evidence import collect_evidence, write_manuscript
from research.paper.model_draft import draft_from_saved_response


def actual_evidence(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    # Authored deterministic fixture: calculate the observed values in process.
    intervals = 10
    points = [(index + .5) * math.pi / intervals for index in range(intervals)]
    integral = sum(math.sin(point) for point in points) * math.pi / intervals
    (run / 'metrics.json').write_text(json.dumps({
        'integral': integral, 'error': abs(integral - 2), 'intervals': intervals,
    }))
    # Produce a valid local image artifact without launching a child process.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    figure, axes = plt.subplots()
    axes.plot(points, [math.sin(point) for point in points])
    axes.set_xlabel('x')
    axes.set_ylabel('sin(x)')
    figure.savefig(run / 'samples.png')
    plt.close(figure)
    (run / 'figures.json').write_text(json.dumps([
        {'id': 'samples', 'path': 'samples.png', 'caption': 'Actual evaluated samples.'}
    ]))
    return collect_evidence(
        [{'id': 'run-1', 'status': 'completed', 'directory': str(run)}],
        [{'id': 'methods', 'title': 'Numerical methods notes',
          'data': {'abstract': 'A supplied description of the numerical method.'}}])


def valid_draft():
    roles = ('introduction', 'related_work', 'method', 'experimental_setup', 'results', 'discussion')
    sections = [
        {'title': role.replace('_', ' ').title(), 'role': role,
         'paragraphs': ['The declared scope is a bounded numerical calculation.']}
        for role in roles
    ]
    sections[4]['blocks'] = [
        {'type': 'table', 'columns': ['Run', 'Integral'],
         'rows': [['Observed', '[[metric:m0]]']],
         'caption': 'The computed integral.', 'label': 'tab:results'},
        {'type': 'figure', 'figure_id': 'run-1:samples', 'width': .65,
         'caption': 'The evaluated samples.', 'label': 'fig:samples'},
    ]
    return {
        'manuscript_type': 'full_paper',
        'title': 'A bounded calculation',
        'abstract': 'The measured integral is [[metric:m0]].',
        'sections': sections,
        'conclusion': 'The recorded calculation is reproducible from its supplied evidence.',
        'claim_ids': [],
    }


def saved_response(tmp_path, draft):
    response_path = tmp_path / 'attempt-1.json'
    response = {
        'text': json.dumps(draft),
        'model': 'recorded-model',
        'usage': {'input_tokens': 12, 'output_tokens': 18},
        'request_id': 'request-1',
        'response_id': 'response-1',
    }
    response_path.write_bytes(json.dumps(response).encode())
    return response_path, response


@pytest.mark.parametrize('invalid_kind', ('numeric_binding', 'statistical_table'))
def test_recorded_revision_repairs_each_invalid_original_without_model_call_or_loss_of_provenance(tmp_path, invalid_kind):
    evidence = actual_evidence(tmp_path)
    original = valid_draft()
    if invalid_kind == 'numeric_binding':
        original['abstract'] = 'The measured integral is [[metric:missing]].'
    else:
        original['sections'][4]['blocks'][0] = {
            'type': 'table',
            'statistics_spec': {'datasets': ['missing-dataset']},
            'argumentative_duty': 'effectiveness',
            'caption': 'Invalid saved table.',
            'label': 'tab:results',
        }
    response_path, response = saved_response(tmp_path, original)
    revision = {'draft': valid_draft(), 'editor': 'Recorded reviewer',
                'reason': 'Bind the empirical value and replace the invalid table.'}
    revision_path = tmp_path / 'reviewed.json'
    revision_path.write_text(json.dumps(revision))
    (tmp_path / 'original-evidence.json').write_text(json.dumps(evidence))
    original_bytes = response_path.read_bytes()
    output = tmp_path / 'reuse'

    draft, reused, provenance = draft_from_saved_response(
        response_path, evidence, output, origin_run_id='origin-1',
        origin_response_path='paper/model_responses/origin/attempt-1.json',
        expected_type='full_paper', original_evidence_path=tmp_path / 'original-evidence.json',
        revision_path=revision_path, revision_project_path='paper/revisions/reviewed.json',
        required_figure_ids=['run-1:samples'])

    assert draft == revision['draft']
    assert reused == response
    assert provenance['mode'] == 'saved_response_with_recorded_revision'
    assert provenance['new_model_requests'] == 0
    assert provenance['new_model_cost_usd'] == 0
    assert provenance['revision']['editor'] == 'Recorded reviewer'
    changes = json.loads((output / 'draft_revision_changes.json').read_bytes())
    expected_pointers = ({'/abstract'} if invalid_kind == 'numeric_binding' else {
        '/sections/4/blocks/0/argumentative_duty', '/sections/4/blocks/0/columns',
        '/sections/4/blocks/0/rows', '/sections/4/blocks/0/statistics_spec',
        '/sections/4/blocks/0/caption'})
    assert {change['json_pointer'] for change in changes} == expected_pointers
    assert provenance['revision']['changed_fields'] == len(expected_pointers)
    assert response_path.read_bytes() == original_bytes
    assert json.loads((output / 'reused_model_response.json').read_bytes()) == response
    assert json.loads((output / 'draft_revision.json').read_bytes()) == revision

    rendered = write_manuscript(output / 'paper', evidence, draft)
    assert rendered['figures'][0]['figure_id'] == 'run-1:samples'
    assert (output / 'paper' / 'paper.tex').is_file()


def test_saved_response_without_revision_still_rejects_invalid_draft(tmp_path):
    evidence = actual_evidence(tmp_path)
    invalid = valid_draft()
    invalid['sections'][4]['blocks'][0] = {
        'type': 'table',
        'statistics_spec': {'datasets': ['missing-dataset']},
        'argumentative_duty': 'effectiveness',
        'caption': 'Invalid saved table.',
        'label': 'tab:results',
    }
    response_path, _ = saved_response(tmp_path, invalid)

    with pytest.raises(ValueError, match='selects unavailable datasets'):
        draft_from_saved_response(response_path, evidence, tmp_path / 'rejected',
                                  origin_run_id='origin-1', origin_response_path='saved.json',
                                  expected_type='full_paper')


def test_recorded_revision_returns_materialized_statistical_table(tmp_path):
    evidence = actual_evidence(tmp_path)
    actual_error = evidence['runs'][0]['metrics']['error']
    record = {'id': 'stat-record', 'dataset': 'grid', 'method': 'midpoint',
              'metric': 'absolute_error', 'direction': 'lower', 'unit': 'integral units',
              'condition': None, 'x': None, 'estimate': actual_error,
              'uncertainty': {'type': 'none'}, 'refs': {'estimate': 'statm0'},
              'source_run_id': 'run-1', 'source_pointer': '/error'}
    evidence['statistics'] = {'version': 1, 'records': [record], 'comparisons': [],
                              'coverage': {'complete': True, 'design_declared': True}}
    evidence['metrics'].append({'id': 'statm0', 'value': actual_error, 'run_id': 'run-1',
                                'pointer': '/error'})
    original = valid_draft()
    response_path, _ = saved_response(tmp_path, original)
    revised = valid_draft()
    revised['abstract'] = 'The measured error is [[metric:statm0]].'
    revised['sections'][4]['blocks'][0] = {
        'type': 'table', 'statistics_spec': {'kind': 'records'},
        'argumentative_duty': 'effectiveness', 'caption': 'Measured error.',
        'label': 'tab:results',
    }
    revision_path = tmp_path / 'reviewed.json'
    revision_path.write_text(json.dumps({'draft': revised, 'editor': 'Reviewer', 'reason': 'Use computed table'}))

    draft, _, _ = draft_from_saved_response(
        response_path, evidence, tmp_path / 'reuse', origin_run_id='origin-1',
        origin_response_path='saved.json', expected_type='full_paper', revision_path=revision_path)

    table = draft['sections'][4]['blocks'][0]
    assert table['rows'] == [['midpoint', '[[metric:statm0]]']]
    assert table['statistics_binding']['record_ids'] == ['stat-record']


def test_revision_keeps_evidence_identity_guard_and_revision_schema(tmp_path):
    evidence = actual_evidence(tmp_path)
    original_evidence_path = tmp_path / 'original-evidence.json'
    original_evidence_path.write_text(json.dumps(evidence))
    response_path, _ = saved_response(tmp_path, valid_draft())
    revision_path = tmp_path / 'reviewed.json'
    revision_path.write_text(json.dumps({'draft': valid_draft(), 'editor': 'Reviewer', 'reason': 'Repair'}))

    changed = deepcopy(evidence)
    changed['runs'][0]['metrics']['integral'] = 3.0
    with pytest.raises(ValueError, match='values or row identities changed'):
        draft_from_saved_response(response_path, changed, tmp_path / 'changed',
                                  origin_run_id='origin-1', origin_response_path='saved.json',
                                  expected_type='full_paper', original_evidence_path=original_evidence_path,
                                  revision_path=revision_path)

    revision_path.write_text(json.dumps({'draft': valid_draft(), 'editor': '', 'reason': 'Missing editor'}))
    with pytest.raises(ValueError, match='requires draft, editor and reason'):
        draft_from_saved_response(response_path, evidence, tmp_path / 'bad-revision',
                                  origin_run_id='origin-1', origin_response_path='saved.json',
                                  expected_type='full_paper', revision_path=revision_path)


def test_revision_still_enforces_final_manuscript_style_contract(tmp_path):
    evidence = actual_evidence(tmp_path)
    response_path, _ = saved_response(tmp_path, valid_draft())
    invalid_final = valid_draft()
    invalid_final['abstract'] = 'This manuscript was generated with AI assistance for the measured result [[metric:m0]].'
    revision_path = tmp_path / 'reviewed.json'
    revision_path.write_text(json.dumps({'draft': invalid_final, 'editor': 'Reviewer', 'reason': 'Test final contract'}))

    with pytest.raises(ValueError, match='AI-writing declarations'):
        draft_from_saved_response(response_path, evidence, tmp_path / 'style-rejected',
                                  origin_run_id='origin-1', origin_response_path='saved.json',
                                  expected_type='full_paper', revision_path=revision_path)


def test_revision_rejects_missing_required_figure(tmp_path):
    evidence = actual_evidence(tmp_path)
    response_path, _ = saved_response(tmp_path, valid_draft())
    revision_draft = valid_draft()
    revision_draft['sections'][4]['blocks'].pop()
    revision_path = tmp_path / 'reviewed.json'
    revision_path.write_text(json.dumps({'draft': revision_draft, 'editor': 'Reviewer', 'reason': 'Use selected figure'}))

    with pytest.raises(ValueError, match='Insert every explicitly selected figure'):
        draft_from_saved_response(
            response_path, evidence, tmp_path / 'figure-rejected', origin_run_id='origin-1',
            origin_response_path='saved.json', expected_type='full_paper', revision_path=revision_path,
            required_figure_ids=['run-1:samples'])
