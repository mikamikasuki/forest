"""Actual arithmetic, evidence identities and vector text; no model substitutes."""
from copy import deepcopy
import json
import math
from pathlib import Path

from pypdf import PdfReader
import pytest

from research.figures.narrative import (
    evidence_catalog, narrative_reference_context, narrative_review_instruction,
    story_design_request, story_image_prompt, validate_storyboard,
)
from research.figures.render import render_figure
from research.figures.workflow import render_candidates, review_requests
from research.agents.policy import RESEARCH_POLICY, ROLES, agent_policy
from research.publication.profile import publication_instructions, publication_profile


def actual_story():
    """A small renderer regression, explicitly not a scientific submission."""
    integers = list(range(100))
    total, independent = sum(integers), math.fsum(integers)
    context = {
        'problem': 'An arithmetic total alone does not compare distinct accumulation procedures.',
        'method': 'Evaluate Python integer sum and math.fsum on the same saved integer operands, then subtract their actual totals.',
        'baseline': 'math.fsum consumes the identical supplied integer operands.',
        'evidence': {'metrics': [
            {'id': 'integer_total', 'value': total, 'unit': 'integer total'},
            {'id': 'float_total', 'value': independent, 'unit': 'integer total'},
            {'id': 'difference', 'value': total - independent, 'unit': 'integer total'},
        ], 'sources': []},
    }
    definition = lambda text: {'text': text, 'status': 'METHOD_DEFINITION', 'evidence_refs': ['context:method']}
    story = {
        'mode': 'scientific_story', 'title': 'Inspect distinct accumulation mechanisms',
        'display': {'scope': 'Arithmetic regression'},
        'central_message': definition('Pair actual outputs rather than trust a single accumulated total.'),
        'problem': {'text': context['problem'], 'status': 'INFERRED', 'evidence_refs': ['context:problem']},
        'consequence': {'text': 'The observed paired difference is [[metric:difference]].', 'status': 'MEASURED', 'evidence_refs': ['difference']},
        'inputs': ['Identical saved integer operands'], 'outputs': ['Two totals and their paired difference'],
        'boundary_conditions': ['This arithmetic regression establishes no general scientific improvement'],
        'mechanism': {'operations': [
            {'id': 'sum', 'label': 'Integer accumulation', 'action': 'Accumulate the supplied operands using Python integer addition.', 'evidence_refs': ['context:method']},
            {'id': 'fsum', 'label': 'Compensated accumulation', 'action': 'Accumulate the identical operands through the math.fsum procedure.', 'evidence_refs': ['context:method']},
            {'id': 'compare', 'label': 'Paired difference', 'action': 'Subtract the actual independent total from the actual integer total.', 'evidence_refs': ['context:method']},
        ], 'edges': [{'source': 'sum', 'target': 'compare', 'label': 'Actual integer total'},
                    {'source': 'fsum', 'target': 'compare', 'label': 'Actual independently accumulated total'}],
            'key_change': {'operation_id': 'compare', 'explanation': 'The comparison stage exposes disagreement between distinct accumulation mechanisms on identical inputs.', 'evidence_refs': ['context:method']}},
        'baseline': {'name': 'math.fsum', 'description': context['baseline'], 'status': 'neutral_structure', 'evidence_refs': ['context:baseline']},
        'composition': {'print_width_in': 6.5, 'minimum_font_pt': 9,
                        'reading_order': ['problem', 'mechanism', 'consequence'],
                        'exact_labels': ['Integer accumulation', 'Compensated accumulation', 'Paired difference']},
    }
    return context, story


def graph_data(context, story):
    return {'nodes': story['mechanism']['operations'], 'edges': story['mechanism']['edges'],
            'storyboard': story, 'story_context': context, 'narrative_mode': 'scientific_story'}


def test_real_measurements_bind_story_and_generic_directory_is_rejected():
    context, story = actual_story()
    validation = validate_storyboard(story, context)
    assert validation['evidence_refs'] == ['context:baseline', 'context:method', 'context:problem', 'difference']
    assert not validation['unmeasured_expectation']
    broken = deepcopy(story)
    broken['mechanism']['operations'][0]['action'] = 'process the input'
    with pytest.raises(ValueError, match='generic module directory'):
        validate_storyboard(broken, context)
    broken = deepcopy(story)
    broken['consequence']['evidence_refs'] = ['context:method']
    with pytest.raises(ValueError, match='without actual supplied measurements'):
        validate_storyboard(broken, context)
    broken = deepcopy(story)
    broken['consequence']['text'] = 'The paired difference is 0.'
    with pytest.raises(ValueError, match='bind measured numeric'):
        validate_storyboard(broken, context)
    broken = deepcopy(story)
    broken['consequence']['text'] = 'The difference is [[metric:unobserved]].'
    with pytest.raises(ValueError, match='unbound measurement'):
        validate_storyboard(broken, context)
    broken = deepcopy(context)
    broken['evidence']['metrics'][0].pop('value')
    with pytest.raises(ValueError, match='actual finite numeric'):
        evidence_catalog(broken)


@pytest.mark.parametrize(('literal', 'visible_only'), [
    ('1e6', False),
    ('-2E6', True),
])
def test_story_image_prompt_rejects_unbound_scientific_notation(literal, visible_only):
    context, story = actual_story()
    claim = f'The observed paired difference is {literal}.'
    if visible_only:
        story['display']['consequence'] = claim
    else:
        story['consequence']['text'] = claim

    with pytest.raises(ValueError, match='bind measured numeric'):
        story_image_prompt(story, context)


def test_baseline_failure_and_unmeasured_expectation_cannot_be_disguised():
    context, story = actual_story()
    broken = deepcopy(story)
    broken['baseline']['description'] = 'The baseline fails and produces inferior outputs.'
    with pytest.raises(ValueError, match='unsupported failure'):
        validate_storyboard(broken, context)
    story['consequence'] = {'text': 'The totals should agree for these bounded operands.', 'status': 'HYPOTHESIS', 'evidence_refs': ['context:method']}
    with pytest.raises(ValueError, match='discriminating test'):
        validate_storyboard(story, context)
    story['consequence']['test'] = 'Execute both actual implementations and compare their saved totals.'
    assert validate_storyboard(story, context)['unmeasured_expectation']
    prompt = story_image_prompt(story, context)
    assert 'Required visible text: "Testable expectation"' in prompt
    assert 'Use case: scientific-educational' in prompt
    assert 'original-resolution' in prompt


def test_explicit_simple_topology_retains_supplied_labels_without_a_fake_story(tmp_path):
    context = {'data': {'nodes': [{'id': 'read', 'label': 'Read actual operands'}, {'id': 'add', 'label': 'Add supplied integers'}],
                        'edges': [{'source': 'read', 'target': 'add', 'label': 'Saved operand list'}]}}
    story = {'mode': 'method_only', 'title': 'Supplied addition procedure', **deepcopy(context['data']),
             'composition': {'print_width_in': 3.125, 'minimum_font_pt': 9}}
    assert validate_storyboard(story, context, 'method_only')['mode'] == 'method_only'
    with pytest.raises(ValueError, match='silently downgrading'):
        validate_storyboard(story, context)
    broken = deepcopy(story); broken['nodes'][0]['label'] = 'Invented reader'
    with pytest.raises(ValueError, match='actual supplied topology'):
        validate_storyboard(broken, context, 'method_only')
    prompt = story_image_prompt(story, context, 'method_only')
    assert 'Testable expectation' not in prompt and 'Read actual operands' in prompt
    assert 'do not reject it for lacking' in narrative_review_instruction('method_only')
    output = render_figure(tmp_path, context['data'], {'layout_width_in': 3.125}, 'method')
    assert PdfReader(output['pdf']).pages[0].mediabox.width / 72 == pytest.approx(3.125)


@pytest.mark.parametrize('width', [3.125, 6.5])
def test_scientific_story_actual_vector_text_print_size_units_and_editable_sources(tmp_path, width):
    context, story = actual_story()
    story['composition']['print_width_in'] = width
    # The narrow-column design requests an explicit taller panel; type is never shrunk.
    if width < 5: story['composition']['print_height_in'] = 5.5
    paths = render_figure(tmp_path, graph_data(context, story), {}, 'method')
    report = json.loads(Path(paths['report']).read_text())
    svg = Path(paths['svg']).read_text()
    page = PdfReader(paths['pdf']).pages[0]
    rendered = ' '.join(page.extract_text().split())
    assert report['reading_order'] == ['problem', 'mechanism', 'consequence']
    assert report['mechanism_area_fraction'] >= .5
    assert page.mediabox.width / 72 == pytest.approx(width)
    assert 'Problem bottleneck' in rendered and 'Key mechanism' in rendered and 'Observed outcome' in rendered
    assert 'Inputs:' in rendered and 'Outputs:' in rendered and 'Boundary conditions:' in rendered
    assert 'integer total' in rendered and '0' in rendered and '[[metric:' not in rendered
    assert report['metric_bindings']['difference']['value'] == context['evidence']['metrics'][-1]['value']
    assert all(label in rendered for label in report['edge_labels'])
    assert all(operation['label'] in rendered for operation in story['mechanism']['operations'])
    assert '<text' in svg and '<svg' in svg
    sizes = []
    page.extract_text(visitor_text=lambda text, cm, tm, font, size: sizes.append(size) if text.strip() else None)
    assert sizes and min(sizes) >= 8 and report['minimum_font_pt'] >= 8
    assert json.loads(Path(paths['data']).read_text())['story_context'] == context
    assert 'render_figure' in Path(paths['source']).read_text()


def test_hypothesis_native_render_and_source_identity_contract(tmp_path):
    context, story = actual_story()
    story['consequence'] = {'text': 'Distinct procedures should agree on the supplied operands.', 'status': 'HYPOTHESIS',
        'evidence_refs': ['context:method'], 'test': 'Compute and inspect both actual totals and their difference.'}
    paths = render_figure(tmp_path/'hypothesis', graph_data(context, story), {}, 'method')
    actual = ' '.join(PdfReader(paths['pdf']).pages[0].extract_text().split())
    assert 'Testable expectation' in Path(paths['svg']).read_text()
    companion=json.loads(Path(paths['caption_context']).read_text())
    assert companion['consequence']['test'] == story['consequence']['test']
    assert 'Discriminating test:' not in actual  # Full test prose belongs to its caption companion.
    assert 'Observed outcome' not in actual
    data = graph_data(context, story); data.pop('story_context')
    with pytest.raises(ValueError, match='actual story_context'):
        render_figure(tmp_path/'missing', data, {}, 'method')
    data = graph_data(context, story); data['edges'] = []
    with pytest.raises(ValueError, match='preserve every actual storyboard'):
        render_figure(tmp_path/'changed', data, {}, 'method')


def test_actual_candidates_have_three_narrative_review_jobs_and_real_source_precedents(tmp_path):
    context, story = actual_story()
    bundle = render_candidates(tmp_path, graph_data(context, story), kind='method')
    jobs = review_requests(bundle, {'narrative_mode': 'scientific_story'})
    assert len(jobs) == 3 and all('Reject a generic module directory' in job['instruction'] for job in jobs)
    simple = review_requests(bundle, {'narrative_mode': 'method_only'})
    assert all('explicitly requested method_only' in job['instruction'] for job in simple)
    references = narrative_reference_context()
    assert len(references['papers']) == 16
    assert all(paper['official_url'] and paper['pdf_url'] and paper['transferable_lesson'] for paper in references['papers'])
    dspy = next(paper for paper in references['papers'] if paper['id'] == 'dspy')
    assert dspy['layout']['main_figure_count'] == 0
    sweagent = next(paper for paper in references['papers'] if paper['id'] == 'sweagent')
    assert sweagent['layout']['figures'][0]['pdf_page'] == 1
    request = story_design_request(context)
    payload = json.loads(request['prompt'])
    assert payload['evidence_catalog']['difference']['record']['value'] == 0
    assert payload['accepted_design_precedents'] == references
    assert 'actual catalog identities' in request['instruction']
    assert 'scientific' in ROLES['Figure Designer'] and 'generic module directories' in ROLES['Visual Selector']
    assert 'SCIENTIFIC VISUAL NARRATIVE' in agent_policy('Figure Designer', {}, publication_profile())
    assert 'cannot remove a core negative result' in agent_policy('Writer', {}, publication_profile())
    assert 'Never change a metric or comparator to manufacture a win' in agent_policy('Writer', {}, publication_profile())
    assert 'uncertain request retains its reserved bound' in RESEARCH_POLICY
    assert 'empty task queue' in RESEARCH_POLICY
    assert 'SCIENTIFIC VISUAL NARRATIVE' in publication_instructions(publication_profile())


def test_actual_retained_story_replays_printable_join_without_changing_source(tmp_path):
    fixture=json.loads(Path(__file__).with_name('fixtures').joinpath('observed_identity_story.json').read_text())
    data=fixture['data'];original=deepcopy(data)
    paths=render_figure(tmp_path,data,{'font_size':9.5,'height':4.4},'method')
    report=json.loads(Path(paths['report']).read_text())
    assert data==original
    assert json.loads(Path(paths['data']).read_text())==original
    page=PdfReader(paths['pdf']).pages[0]
    assert page.mediabox.width/72==pytest.approx(6.5)
    assert page.mediabox.height/72==pytest.approx(4.4)
    assert report['minimum_font_pt']==9.5
    assert report['representation_branches']==['Table','Prose','Vector heatmap']
    assert report['example_kind']=='record_binding'
    assert report['concrete_example_identity']=={'dataset':'Breast cancer','method':'Logistic'}
    assert report['metric_bindings']=={m['id']:m for m in data['story_context']['metrics']}
    source_mean=data['story_context']['metrics'][0]
    populated=report['populated_comparison_cells']
    assert len(populated)==1 and populated[0]['binding']=='B1'
    assert populated[0]['representation']=='Table'
    assert populated[0]['metric_ref']==source_mean['id']
    assert populated[0]['exact_value']==source_mean['value']
    assert populated[0]['display_value']==format(source_mean['value'],'.6g')
    assert len(report['bound_quantities'])==2
    svg=Path(paths['svg']).read_text()
    assert '0.970629' in svg and '0.0103723' in svg
    assert svg.count(format(source_mean['value'],'.6g'))>=3  # Leaf, joined record and populated cell.
    assert 'Accuracy = 0.970629' in svg and 'SD = 0.0103723' in svg
    assert '/summary/0/accuracy' in svg and '/summary/0/accuracy_std' in svg
    assert 'causal zoom' not in svg
    assert 'B1' in svg and 'Run R1' in svg and 'rounded leaves' in svg
    assert 'Discriminating test:' in svg and 'compiler positions' in svg and '(if available)' in svg
    assert 'Show the concrete cell' not in svg and 'dominant visual zoom should' not in svg
    assert 'fixture-run-001' not in svg
    companion=json.loads(Path(paths['caption_context']).read_text())
    assert companion['operation_rationale']==data['nodes']
    assert companion['boundary_conditions']==data['storyboard']['boundary_conditions']
    assert companion['consequence']['test']==data['storyboard']['consequence']['test']
    assert companion['run_aliases']['R1']=='fixture-run-001'
    assert '5 random splits reuse source samples' in companion['caption_text']
    assert companion['binding_aliases']['B1']['identity']==report['concrete_example_identity']
    assert len(report['edge_label_geometry'])==4
    # Printed labels remain beside their own true edge, not in a detached legend.
    for edge in report['edge_label_geometry'][:3]:
        assert edge['start_in'][1]==pytest.approx(edge['end_in'][1])
        assert abs(edge['label_position_in'][1]-edge['start_in'][1])<.1


def test_dataset_method_metadata_alone_never_invents_a_record_binding_method(tmp_path):
    fixture=json.loads(Path(__file__).with_name('fixtures').joinpath('observed_identity_story.json').read_text())
    data=deepcopy(fixture['data']);story=data['storyboard']
    # Keep exactly the same observed measurements/parent records, but request a
    # distinct supplied scientific operation. Record identity is not a method.
    operation={'id':'inspect','label':'Inspect supplied observations',
        'action':'Inspect the supplied quantities under the actual uncertainty definition.',
        'display_transform':'Compare actual source quantities',
        'evidence_refs':[m['id'] for m in data['story_context']['metrics']]}
    story['mechanism']['operations']=[operation];story['mechanism']['edges']=[]
    story['mechanism']['key_change']['operation_id']='inspect'
    story['composition']['exact_labels']=['Inspect supplied observations']
    story['display']={'problem':'Inspect supplied quantities.', 'consequence':'Reporting-error reduction remains unmeasured.', 'scope':'Engineering qualification'}
    story['mechanism']['example']={'kind':'measurement_context','operation_id':'inspect','metric_refs':operation['evidence_refs'][:2]}
    data['nodes']=story['mechanism']['operations'];data['edges']=[]
    paths=render_figure(tmp_path,data,{},'method')
    report=json.loads(Path(paths['report']).read_text())
    assert report['example_kind']=='measurement_context'
    assert report['representation_branches']==[]
    assert '>join<' not in Path(paths['svg']).read_text()
    assert report['metric_bindings'].keys()==set(operation['evidence_refs'][:2])


def test_dense_story_is_rejected_without_poster_expansion_or_metric_omission(tmp_path):
    context,story=actual_story()
    story['mechanism']['operations'][0]['label']=' '.join(['Grounded operation']*100)
    with pytest.raises(ValueError,match='print size|physical canvas|separate panels'):
        render_figure(tmp_path/'text',graph_data(context,story),{},'method')
    context,story=actual_story()
    context['evidence']['metrics'].extend({'id':'operand_'+str(i),'value':i} for i in range(5))
    story['mechanism']['operations'][0]['evidence_refs'].extend('operand_'+str(i) for i in range(5))
    with pytest.raises(ValueError,match='never silently omitted'):
        render_figure(tmp_path/'selection',graph_data(context,story),{},'method')


def test_visible_measured_claim_and_parent_record_cannot_change_observations(tmp_path):
    context,story=actual_story()
    story['display']['consequence']='Observed difference is 100.'
    with pytest.raises(ValueError,match='bind measured numeric'):
        validate_storyboard(story,context)
    fixture=json.loads(Path(__file__).with_name('fixtures').joinpath('observed_identity_story.json').read_text())
    data=fixture['data']
    data['story_context']['metrics'][0]['value']+=.001
    with pytest.raises(ValueError,match='actual run-scoped parent pointer'):
        render_figure(tmp_path,data,{},'method')
