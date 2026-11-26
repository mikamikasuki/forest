"""Actual source insertion and transport construction, without model substitutes."""
from pathlib import Path
from copy import deepcopy
import json
import pytest

from research.paper.insertion import insert_figure
from research.paper.style import ai_declarations
from research.paper.manuscript import compile_paper
from research.figures.render import render_figure
from research.figures.model_workflow import image_content
from research.agents.provider import ModelClient
from research.paper.evidence import collect_evidence
from research.paper.model_draft import validate_manuscript_contract
from research.paper.visual_review import placement_requests


def test_insertion_keeps_exact_author_prose_and_compiles_with_a_real_asset(tmp_path):
    paragraph='The observed sums increase with the sequence endpoint.'
    source = '\n'.join([
        r'\documentclass{article}', r'\usepackage{hyperref}', r'\begin{document}',
        r'Reference: \url{https://example.org/report%20files}.',
        r'Appendix: \href[page=2]{https://example.org/data%20appendix.pdf}{linked PDF}.',
        r'\section{Results}', paragraph, '', 'A separate paragraph remains intact.',
        r'\end{document}', '',
    ])
    rows=[{'dataset':'integer sequence','method':str(n),'score':sum(range(n))} for n in range(3,15)]
    paths=render_figure(tmp_path/'figures'/'sums',rows,{'metric':'score','unit':'integer sum','layout_width_in':6.5,'font_size':9})
    result=insert_figure(source,asset_path='figures/sums/figure.pdf',caption='All twelve computed sequence sums.',label='fig:sums',anchor_text=paragraph)
    assert result.index(paragraph)<result.index('\\begin{figure}')<result.index('A separate paragraph remains intact.')
    assert '\\usepackage{graphicx}' in result
    (tmp_path/'paper.tex').write_text(result)
    compiled=compile_paper(tmp_path)
    if compiled['status']=='unavailable':pytest.skip('Actual TeX compiler required')
    assert compiled['status']=='completed',compiled['log']
    assert compiled['preflight']['checks']['assets']=='passed'
    assert Path(paths['pdf']).read_bytes().startswith(b'%PDF')


def test_anchor_and_duplicate_labels_are_exact_and_unavailable_assets_not_invented():
    source='\\documentclass{article}\n\\begin{document}\nMeasured text.\nMeasured text.\n\\end{document}'
    args={'asset_path':'figures/a/figure.png','caption':'Observed output.','label':'fig:a','anchor_text':'Measured text.'}
    with pytest.raises(ValueError,match='unique'):insert_figure(source,**args)
    args['anchor_text']='article'
    with pytest.raises(ValueError,match='prose'):insert_figure(source,**args)
    args['anchor_text']='Measured text.\nMeasured text.'
    once=insert_figure(source,**args)
    with pytest.raises(ValueError,match='already'):insert_figure(once,**args)


def test_authorship_boilerplate_is_distinct_from_ai_method_research():
    assert ai_declarations('This manuscript was written using ChatGPT.')
    assert ai_declarations('AI-use statement')
    assert not ai_declarations('Our agent uses a language model to generate experiment code. The model is the method under evaluation.')


def authored_visual_contract(tmp_path):
    """Recorded arithmetic and authored prose; no model-response substitutes."""
    run=tmp_path/'actual-arithmetic';run.mkdir()
    values=[sum(range(n)) for n in range(3,15)]
    (run/'metrics.json').write_text(json.dumps({'integer_totals':values}))
    rows=[{'dataset':'integer sequence','method':str(n),'score':sum(range(n))} for n in range(3,15)]
    outputs=render_figure(run/'comparisons',rows,{'metric':'score','unit':'integer sum'})
    line_rows=[{'method':'sum','endpoint':n,'score':sum(range(n))} for n in range(3,15)]
    line=render_figure(run/'endpoints',line_rows,{'metric':'score','unit':'integer sum','x':'endpoint'},'line')
    (run/'figures.json').write_text(json.dumps([
        {'id':'comparison','path':str(Path(outputs['pdf']).relative_to(run))},
        {'id':'endpoints','path':str(Path(line['pdf']).relative_to(run))}]))
    evidence=collect_evidence([{'id':'arithmetic','status':'completed','directory':str(run)}])
    draft={'manuscript_type':'research_note','title':'Authored arithmetic contract verification',
        'abstract':'The first computed sum is [[metric:m0]].','conclusion':'The recorded arithmetic totals are preserved.',
        'sections':[{'title':'Results','paragraphs':['Figure [[ref:fig:comparison]] gives all recorded integer totals; Figure [[ref:fig:endpoints]] relates them to sequence endpoints.'],
            'blocks':[{'type':'figure','figure_id':figure['id'],'label':'fig:'+figure['id'].split(':')[-1],
                       'caption':'Recorded sequence sums.'} for figure in evidence['figures']]}], 'claim_ids':[]}
    return evidence,draft


def test_one_contract_requires_all_explicitly_selected_real_assets(tmp_path):
    evidence,draft=authored_visual_contract(tmp_path)
    required=[figure['id'] for figure in evidence['figures']]
    validated=validate_manuscript_contract(draft,evidence,'research_note',required_figure_ids=required)
    assert validated['referenced_figures']==sorted(required)
    assert validated['required_figure_ids']==required
    omitted=deepcopy(draft)
    omitted['sections'][0]['paragraphs']=['The first figure presents the recorded arithmetic totals.']
    omitted['sections'][0]['blocks']=omitted['sections'][0]['blocks'][:1]
    with pytest.raises(ValueError,match='every explicitly selected figure'):
        validate_manuscript_contract(omitted,evidence,'research_note',required_figure_ids=required)
    # Unselected evidence is optional; selected assets are an explicit contract.
    assert validate_manuscript_contract(omitted,evidence,'research_note')['referenced_figures']==required[:1]


@pytest.mark.parametrize('field',['abstract','conclusion','section_title','caption','paragraph'])
def test_final_authored_manuscript_rejects_boilerplate_in_every_prose_location(tmp_path,field):
    evidence,draft=authored_visual_contract(tmp_path)
    statement='This manuscript was written using ChatGPT.'
    if field in ('abstract','conclusion'):
        draft[field]+=' '+statement
    elif field=='section_title':
        draft['sections'][0]['title']+=' '+statement
    elif field=='caption':
        draft['sections'][0]['blocks'][0]['caption']+=' '+statement
    else:
        draft['sections'][0]['paragraphs'][0]+=' '+statement
    with pytest.raises(ValueError,match='AI-writing declarations'):
        validate_manuscript_contract(draft,evidence,'research_note')


def test_explicit_figure_contract_rejects_unavailable_or_duplicate_ids(tmp_path):
    evidence,draft=authored_visual_contract(tmp_path)
    required=evidence['figures'][0]['id']
    for invalid in ([required,required],required,[None]):
        with pytest.raises(ValueError,match='unique actual figure'):
            validate_manuscript_contract(draft,evidence,'research_note',required_figure_ids=invalid)
    with pytest.raises(ValueError,match='unavailable in the supplied evidence'):
        validate_manuscript_contract(draft,evidence,'research_note',required_figure_ids=['unavailable'])


def test_author_revision_repairs_the_final_selected_asset_and_boilerplate_contract(tmp_path):
    evidence,authored=authored_visual_contract(tmp_path)
    required=[figure['id'] for figure in evidence['figures']]
    earlier=deepcopy(authored)
    earlier['sections'][0]['paragraphs']=['The arithmetic totals were computed locally.']
    earlier['sections'][0]['blocks']=earlier['sections'][0]['blocks'][:1]
    earlier['conclusion']+=' This manuscript was written using ChatGPT.'
    with pytest.raises(ValueError,match='every explicitly selected figure'):
        validate_manuscript_contract(earlier,evidence,'research_note',required_figure_ids=required)
    # A documented author revision restores the selected actual plot and removes
    # boilerplate. It requires no substitute provider response or model receipt.
    assert validate_manuscript_contract(authored,evidence,'research_note',required_figure_ids=required)['referenced_figures']==sorted(required)


def test_all_appendix_and_main_visuals_receive_real_paragraph_choices(tmp_path):
    _,draft=authored_visual_contract(tmp_path)
    draft['appendices']=[{'title':'Arithmetic identities','blocks':[
        {'type':'paragraph','text':'Table [[ref:tab:appendix]] retains the computed first total.'},
        {'type':'table','columns':['Total'],'rows':[['[[metric:m0]]']],
         'label':'tab:appendix','caption':'Actual integer total.','argumentative_duty':'alternative_explanation'}]}]
    prepared,requests=placement_requests(draft)
    assert [request['label'] for request in requests]==['fig:comparison','fig:endpoints','tab:appendix']
    appendix=requests[-1]
    assert appendix['appendix'] and appendix['manuscript_scope']=='appendix'
    assert appendix['section_title']=='Arithmetic identities'
    assert appendix['paragraphs'][0]['id']=='block-paragraph-0'
    assert 'id' not in draft['appendices'][0]['blocks'][0]
    assert prepared['appendices'][0]['blocks'][0]['id']=='block-paragraph-0'


@pytest.mark.parametrize('api',['responses','chat_completions','ollama'])
def test_real_candidate_pixels_reach_each_transport(tmp_path,api):
    paths=render_figure(tmp_path/'plot',[{'dataset':'sequence','method':str(n),'score':sum(range(n))} for n in range(4,11)],{'metric':'score','unit':'integer sum'})
    client=ModelClient({'kind':'ollama' if api=='ollama' else 'openai','base_url':'http://127.0.0.1:1/v1','model':'explicit-inspection-model','config':{'api':api}})
    message=image_content(client,[paths['png']],'Inspect these actual computed observations.')
    _,payload=client.build_request([message])
    if api=='ollama':
        from research.agents.policy import RESEARCH_POLICY_MARKER
        assert RESEARCH_POLICY_MARKER in payload['messages'][0]['content']
        observed=next(item for item in payload['messages'] if item['role']=='user')
        assert observed['images']==message['images'] and isinstance(observed['content'],str)
    else:
        import base64,io,re
        from PIL import Image
        match=re.search(r'data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)',json.dumps(payload))
        assert match
        with Image.open(io.BytesIO(base64.b64decode(match.group(2)))) as preview:
            assert preview.format.lower()==match.group(1)
            assert 0<preview.width<=768 and 0<preview.height<=768
