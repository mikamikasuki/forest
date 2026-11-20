"""Real API, SQLite, file publication and standalone LaTeX export regressions.

Small manuscript fixtures test edit protection without scientific-data fixtures
or model responses. Each case runs in its own application/database process.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest

ROOT = Path(__file__).resolve().parents[1]


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def project(client):
    return ok(client.post('/api/projects', json={'name': 'Paper safety', 'goal': 'Preserve authored text'}))['id']


def document(label):
    return ('\\documentclass{article}\n\\usepackage{forest_test}\n'
            '\\input{results_macros.tex}\n\\begin{document}\n' + label +
            '\\ResultTest\n\\end{document}\n')


def bundle(project_id, name, label):
    from services.api.common import project_dir
    folder = project_dir(project_id) / 'runs' / name / 'paper'
    folder.mkdir(parents=True, exist_ok=True)
    source = document(label)
    (folder / 'paper.tex').write_text(source)
    (folder / 'references.bib').write_text('% bibliography ' + label)
    (folder / 'results_macros.tex').write_text('\\newcommand{\\ResultTest}{42}\n% ' + label)
    (folder / 'forest_test.sty').write_text('\\ProvidesPackage{forest_test}\n')
    return {'source': source, 'bibtex': '% bibliography ' + label,
            'source_dir': str(folder.relative_to(project_dir(project_id))),
            'source_run_ids': [], 'bindings': []}


def enqueue_generation(project_id, **config):
    from services.api.db import Session, asdict
    from services.worker.scheduler import enqueue
    with Session.begin() as session:
        return asdict(enqueue(session, project_id, 'paper_generate', config))


def publish(project_id, run, data):
    from services.api.db import Session
    from services.api.paper_state import publish_generation
    with Session.begin() as session:
        return publish_generation(session, project_id, 'A generated draft', data,
                                  run['config']['paper_snapshot'], run['id'])


def case_manual_edits_proposal_and_safe_apply(client):
    from services.api.common import project_dir
    pid = project(client)
    initial = publish(pid, enqueue_generation(pid), bundle(pid, 'initial', 'Initial text. '))
    assert initial['publication'] == 'applied'
    paper = ok(client.get('/api/papers/' + pid))
    pending = enqueue_generation(pid)
    assert pending['config']['paper_snapshot']['revision'] == paper['revision']
    manual = ok(client.patch('/api/papers/' + pid, json={
        'expected_revision': paper['revision'],
        'data': {'source': document('Owner wording. '), 'bibtex': '% Owner bibliography', 'manually_edited': False}}))
    assert manual['data']['manually_edited'] is True
    stable = project_dir(pid) / 'paper'
    assert (stable / 'references.bib').read_text() == '% Owner bibliography'
    original_macro = (stable / 'results_macros.tex').read_text()
    proposal = publish(pid, pending, bundle(pid, 'proposal', 'Replacement text. '))
    assert proposal['publication'] == 'proposed'
    assert ok(client.get('/api/papers/' + pid))['data']['source'] == manual['data']['source']
    assert (stable / 'paper.tex').read_text() == manual['data']['source']
    assert (stable / 'results_macros.tex').read_text() == original_macro
    review = ok(client.get('/api/reviews/' + proposal['review_id']))
    assert '-Owner wording.' in review['data']['diff'] and '+Replacement text.' in review['data']['diff']
    newer = ok(client.patch('/api/papers/' + pid, json={
        'expected_revision': manual['revision'], 'data': {'source': document('Latest owner wording. ')}}))
    stale = client.post('/api/reviews/' + proposal['review_id'] + '/apply', json={
        'expected_revision': newer['revision'], 'indices': [0]})
    assert stale.status_code == 409
    fresh = publish(pid, enqueue_generation(pid), bundle(pid, 'accepted', 'Approved replacement. '))
    assert fresh['publication'] == 'proposed'  # Matching revision still protects manual edits.
    invalid = client.post('/api/reviews/' + fresh['review_id'] + '/apply', json={
        'expected_revision': newer['revision'], 'indices': []})
    assert invalid.status_code == 422
    accepted = ok(client.post('/api/reviews/' + fresh['review_id'] + '/apply', json={
        'expected_revision': newer['revision'], 'indices': [0]}))
    assert accepted['data']['content_origin'] == 'accepted_generation'
    assert accepted['revision'] == newer['revision'] + 1
    assert (stable / 'paper.tex').read_text() == document('Approved replacement. ')
    assert 'Approved replacement.' in (stable / 'results_macros.tex').read_text()
    assert ok(client.get('/api/reviews/' + fresh['review_id']))['status'] == 'applied'


def case_failed_second_file_restores_the_manuscript_pair(client):
    from fastapi.testclient import TestClient
    from services.api.common import project_dir
    from services.api.main import app
    pid = project(client)
    publish(pid, enqueue_generation(pid), bundle(pid, 'initial', 'Initial text. '))
    paper = ok(client.get('/api/papers/' + pid))
    stable = project_dir(pid) / 'paper'
    old_source = (stable / 'paper.tex').read_text()
    old_bibtex = (stable / 'references.bib').read_text()
    revisions = {name: ok(client.get('/api/projects/' + pid + '/file', params={'path': 'paper/' + name}))['revision']
                 for name in ('paper.tex', 'references.bib')}
    replace = Path.replace
    staged = []

    def failing_second_replace(self, target):
        if self.name.endswith('.forest-tmp'):
            staged.append(self.name)
            if len(staged) == 2:
                raise OSError('injected failure on the second file')
        return replace(self, target)

    Path.replace = failing_second_replace
    try:
        failed = TestClient(app, raise_server_exceptions=False).patch('/api/papers/' + pid, json={
            'expected_revision': paper['revision'],
            'data': {'source': document('New text. '), 'bibtex': '% New bibliography'}})
    finally:
        Path.replace = replace
    assert failed.status_code == 500
    assert len(staged) == 2
    assert (stable / 'paper.tex').read_text() == old_source
    assert (stable / 'references.bib').read_text() == old_bibtex
    assert not list(stable.glob('*.forest-tmp'))
    assert ok(client.get('/api/papers/' + pid))['revision'] == paper['revision']
    for name, revision in revisions.items():
        assert ok(client.get('/api/projects/' + pid + '/file', params={'path': 'paper/' + name}))['revision'] == revision


def case_newer_generation_and_simultaneous_publication(client):
    pid = project(client)
    initial = publish(pid, enqueue_generation(pid), bundle(pid, 'initial', 'Initial. '))
    first = enqueue_generation(pid, paper_snapshot={'revision': 999, 'manually_edited': False})
    second = enqueue_generation(pid)
    assert first['config']['paper_snapshot']['revision'] == initial['paper_revision']
    first_data = bundle(pid, 'first', 'First generated. ')
    second_data = bundle(pid, 'second', 'Second generated. ')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda item: publish(pid, *item), [(first, first_data), (second, second_data)]))
    assert sorted(result['publication'] for result in results) == ['applied', 'proposed']
    current = ok(client.get('/api/papers/' + pid))
    assert current['revision'] == initial['paper_revision'] + 1
    losing = next(result for result in results if result['publication'] == 'proposed')
    review = ok(client.get('/api/reviews/' + losing['review_id']))
    assert review['data']['paper_revision'] == current['revision']
    assert review['data']['edits'][0]['original'] == current['data']['source']


def case_span_review_and_legacy_edit_protection(client):
    from services.api.db import Session, PaperDocument, Review
    from services.api.common import project_dir
    pid = project(client)
    publish(pid, enqueue_generation(pid), bundle(pid, 'initial', 'Original phrase. '))
    paper = ok(client.get('/api/papers/' + pid))
    with Session.begin() as session:
        review = Review(project_id=pid, title='Minimal wording proposal', data={
            'paper_id': paper['id'], 'paper_revision': paper['revision'],
            'edits': [{'original': 'Original phrase.', 'replacement': 'Precise phrase.', 'reason': 'Specific wording'}]}, status='proposed')
        session.add(review); session.flush(); review_id = review.id
    edited = ok(client.post('/api/reviews/' + review_id + '/apply', json={
        'expected_revision': paper['revision'], 'indices': [0]}))
    assert edited['data']['manually_edited'] is True
    assert 'Precise phrase.' in (project_dir(pid) / 'paper' / 'paper.tex').read_text()
    # Existing projects without the newly introduced flag also retain edits.
    with Session.begin() as session:
        old = session.get(PaperDocument, paper['id'])
        old.data = {k: v for k, v in old.data.items() if k not in ('manually_edited', 'content_origin')}
    run = enqueue_generation(pid)
    assert run['config']['paper_snapshot']['manually_edited'] is True
    assert publish(pid, run, bundle(pid, 'new', 'New generated. '))['publication'] == 'proposed'


def case_span_offsets_apply_selected_repeated_occurrences(client):
    from services.api.db import Session, Review
    from services.api.common import project_dir
    from research.paper.writing import validate_revision_proposal
    pid = project(client)
    publish(pid, enqueue_generation(pid), bundle(pid, 'initial', 'Initial. '))
    paper = ok(client.get('/api/papers/' + pid))
    source = 'Keep neighbor. marker here. Keep neighbor. marker there.'
    paper = ok(client.patch('/api/papers/' + pid, json={
        'expected_revision': paper['revision'], 'data': {'source': source}}))
    selections = [
        ([0], 'Keep neighbor. marker plus marker here. Keep neighbor. marker there.'),
        ([1], 'Keep neighbor. marker here. Keep neighbor. second replacement there.'),
        ([1, 0], 'Keep neighbor. marker plus marker here. Keep neighbor. second replacement there.'),
    ]
    for indices, expected in selections:
        paper = ok(client.get('/api/papers/' + pid))
        if paper['data']['source'] != source:
            paper = ok(client.patch('/api/papers/' + pid, json={
                'expected_revision': paper['revision'], 'data': {'source': source}}))
        first = source.index('marker')
        second = source.index('marker', first + 1)
        proposal = validate_revision_proposal(source, {'edits': [
            {'original': 'marker', 'replacement': 'marker plus marker', 'start': first},
            {'original': 'marker', 'replacement': 'second replacement', 'start': second},
        ]})
        with Session.begin() as session:
            review = Review(project_id=pid, title='Repeated span proposal', data={
                'paper_id': paper['id'], 'paper_revision': paper['revision'], 'edits': proposal['edits']}, status='proposed')
            session.add(review); session.flush(); review_id = review.id
        applied = ok(client.post('/api/reviews/' + review_id + '/apply', json={
            'expected_revision': paper['revision'], 'indices': indices}))
        assert applied['data']['source'] == expected
        assert applied['revision'] == paper['revision'] + 1
        assert applied['status'] == 'needs_update'
        assert (project_dir(pid) / 'paper' / 'paper.tex').read_text() == expected
        assert ok(client.get('/api/reviews/' + review_id))['status'] == 'applied'

    current = ok(client.get('/api/papers/' + pid))
    before_file = (project_dir(pid) / 'paper' / 'paper.tex').read_text()
    first = source.index('marker')
    second = source.index('marker', first + 1)
    proposal = validate_revision_proposal(source, {'edits': [
        {'original': 'marker', 'replacement': 'marker plus marker', 'start': first},
        {'original': 'marker', 'replacement': 'second replacement', 'start': second},
    ]})
    with Session.begin() as session:
        stale = Review(project_id=pid, title='Stale saved span proposal', data={
            'paper_id': current['id'], 'paper_revision': current['revision'] - 1,
            'edits': proposal['edits']}, status='proposed')
        session.add(stale); session.flush(); stale_id = stale.id
    rejected = client.post('/api/reviews/' + stale_id + '/apply', json={
        'expected_revision': current['revision'], 'indices': [0]})
    assert rejected.status_code == 409
    assert ok(client.get('/api/papers/' + pid))['data']['source'] == current['data']['source']
    assert ok(client.get('/api/papers/' + pid))['revision'] == current['revision']
    assert (project_dir(pid) / 'paper' / 'paper.tex').read_text() == before_file
    assert ok(client.get('/api/reviews/' + stale_id))['status'] == 'proposed'


def case_span_selection_validation_is_atomic(client):
    from services.api.db import Session, Review
    from services.api.common import project_dir
    pid = project(client)
    publish(pid, enqueue_generation(pid), bundle(pid, 'initial', 'Initial. '))
    paper = ok(client.get('/api/papers/' + pid))
    source = 'Repeated marker. Keep neighbor. Repeated marker.'
    paper = ok(client.patch('/api/papers/' + pid, json={
        'expected_revision': paper['revision'], 'data': {'source': source}}))
    file_path = project_dir(pid) / 'paper' / 'paper.tex'
    before_file = file_path.read_text()
    first = source.index('marker')
    second = source.index('marker', first + 1)
    cases = [
        ([{'original': 'marker', 'replacement': 'changed', 'start': 'bad', 'end': first + 6}], [0], 409),
        ([{'original': 'marker', 'replacement': 'changed', 'start': len(source), 'end': len(source) + 6}], [0], 409),
        ([{'original': 'marker', 'replacement': 'changed', 'start': first + 1, 'end': first + 7}], [0], 409),
        ([{'original': 'Repeated marker', 'replacement': 'changed', 'start': 0, 'end': 15},
          {'original': 'marker', 'replacement': 'changed', 'start': first, 'end': first + 6}], [0, 1], 409),
        ([{'original': 'marker', 'replacement': 'changed', 'start': first, 'end': first + 6}], [0, 0], 422),
    ]
    for edits, indices, status in cases:
        with Session.begin() as session:
            review = Review(project_id=pid, title='Invalid span proposal', data={
                'paper_id': paper['id'], 'paper_revision': paper['revision'], 'edits': edits}, status='proposed')
            session.add(review); session.flush(); review_id = review.id
        rejected = client.post('/api/reviews/' + review_id + '/apply', json={
            'expected_revision': paper['revision'], 'indices': indices})
        assert rejected.status_code == status
        assert ok(client.get('/api/papers/' + pid))['data']['source'] == source
        assert ok(client.get('/api/papers/' + pid))['revision'] == paper['revision']
        assert file_path.read_text() == before_file
        assert ok(client.get('/api/reviews/' + review_id))['status'] == 'proposed'
    with Session.begin() as session:
        stale = Review(project_id=pid, title='Stale span proposal', data={
            'paper_id': paper['id'], 'paper_revision': paper['revision'], 'edits': [
                {'original': 'marker', 'replacement': 'changed', 'start': first, 'end': first + 6}]}, status='proposed')
        session.add(stale); session.flush(); stale_id = stale.id
    rejected = client.post('/api/reviews/' + stale_id + '/apply', json={
        'expected_revision': paper['revision'] - 1, 'indices': [0]})
    assert rejected.status_code == 409
    assert ok(client.get('/api/papers/' + pid))['data']['source'] == source
    assert file_path.read_text() == before_file
    assert ok(client.get('/api/reviews/' + stale_id))['status'] == 'proposed'


def case_files_and_paper_studio_share_edit_protection(client):
    pid = project(client)
    publish(pid, enqueue_generation(pid), bundle(pid, 'initial', 'Initial. '))
    paper = ok(client.get('/api/papers/' + pid))
    file_url = '/api/projects/' + pid + '/file'
    opened = ok(client.get(file_url, params={'path': 'paper/paper.tex'}))
    studio = ok(client.patch('/api/papers/' + pid, json={
        'expected_revision': paper['revision'], 'data': {'source': document('Paper Studio wording. ')}}))
    stale_file = client.put(file_url, json={'path': 'paper/paper.tex',
                                          'content': document('Stale Files wording. '),
                                          'expected_revision': opened['revision']})
    assert stale_file.status_code == 409
    queued = enqueue_generation(pid)
    current = ok(client.get(file_url, params={'path': 'paper/paper.tex'}))
    assert current['revision'] == opened['revision'] + 1
    saved = ok(client.put(file_url, json={'path': 'paper/paper.tex', 'content': document('Fresh Files wording. '),
                                         'expected_revision': current['revision']}))
    updated = ok(client.get('/api/papers/' + pid))
    assert updated['revision'] == studio['revision'] + 1
    assert updated['data']['source'] == document('Fresh Files wording. ')
    assert updated['data']['manually_edited'] and updated['status'] == 'needs_update'
    assert client.patch('/api/papers/' + pid, json={'expected_revision': studio['revision'],
                                                  'data': {'source': document('Stale Studio wording. ')}}).status_code == 409
    bibliography = ok(client.get(file_url, params={'path': 'paper/references.bib'}))
    ok(client.put(file_url, json={'path': 'paper/references.bib', 'content': '% Files bibliography',
                                  'expected_revision': bibliography['revision']}))
    revised = ok(client.get('/api/papers/' + pid))
    assert revised['revision'] == updated['revision'] + 1
    assert revised['data']['bibtex'] == '% Files bibliography'
    assert publish(pid, queued, bundle(pid, 'replacement', 'Generated replacement. '))['publication'] == 'proposed'
    assert ok(client.get(file_url, params={'path': 'paper/paper.tex'}))['revision'] == saved['revision']
    assert ok(client.get('/api/papers/' + pid))['data']['source'] == revised['data']['source']
    # Ordinary run artifacts remain editable, but are not working manuscript aliases.
    ok(client.put(file_url, json={'path': 'runs/initial/paper/paper.tex',
                                  'content': document('Edited earlier artifact. '), 'expected_revision': 0}))
    after = ok(client.get('/api/papers/' + pid))
    assert after['revision'] == revised['revision'] and after['data']['source'] == revised['data']['source']


def case_files_creates_protected_manuscript_before_studio(client):
    pid = project(client)
    file_url = '/api/projects/' + pid + '/file'
    ok(client.put(file_url, json={'path': 'paper/paper.tex', 'content': document('Imported authored source. '),
                                  'expected_revision': 0}))
    paper = ok(client.get('/api/papers/' + pid))
    assert paper['revision'] == 1 and paper['data']['manually_edited']
    assert paper['data']['source'] == document('Imported authored source. ')
    assert publish(pid, enqueue_generation(pid), bundle(pid, 'new', 'Generated. '))['publication'] == 'proposed'


def case_standalone_source_zip_compiles(client):
    from services.api.common import project_dir
    from research.paper.manuscript import compile_paper
    pid = project(client)
    data = bundle(pid, 'compiled', 'An exported result: ')
    source = project_dir(pid) / data['source_dir']
    compiled = compile_paper(source)
    assert compiled['status'] == 'completed', compiled['log']
    figure = source / 'figures' / 'nested' / 'figure.pdf'
    figure.parent.mkdir(parents=True)
    shutil.copy2(source / 'paper.pdf', figure)
    data['source'] = data['source'].replace('\\usepackage{forest_test}', '\\usepackage{forest_test,graphicx}').replace(
        '\\end{document}', '\\includegraphics[width=.2\\linewidth]{figures/nested/figure.pdf}\n\\end{document}')
    (source / 'paper.tex').write_text(data['source'])
    compiled = compile_paper(source)
    assert compiled['status'] == 'completed', compiled['log']
    data['pdf_path'] = str((source / 'paper.pdf').relative_to(project_dir(pid)))
    publish(pid, enqueue_generation(pid), data)
    response = client.post('/api/papers/' + pid + '/export', json={'format': 'source'})
    assert response.status_code == 200, response.text
    destination = project_dir(pid).parent / 'standalone-source'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert {'paper.tex', 'references.bib', 'results_macros.tex', 'forest_test.sty', 'figures/nested/figure.pdf', 'paper.pdf'} <= set(names)
        assert len(names) == len(set(names)) and not any(name.startswith('paper/') for name in names)
        archive.extractall(destination)
    # Export must compile outside the project with no access to its assets.
    (destination / 'paper.pdf').unlink()
    result = compile_paper(destination)
    assert result['status'] == 'completed', result['log']
    paper = ok(client.get('/api/papers/' + pid))
    ok(client.patch('/api/papers/' + pid, json={'expected_revision': paper['revision'],
                                             'data': {'source': document('Current edited export: ')}}))
    edited_zip = client.post('/api/papers/' + pid + '/export', json={'format': 'source'})
    with zipfile.ZipFile(io.BytesIO(edited_zip.content)) as archive:
        assert 'Current edited export:' in archive.read('paper.tex').decode()
        assert 'paper.pdf' not in archive.namelist()  # No stale PDF paired with edited source.


def workspace_agent(project_id):
    from services.api.db import Session
    from services.api.common import project_dir
    from services.worker.scheduler import enqueue
    with Session.begin() as session:
        run = enqueue(session, project_id, 'agent', {'instructions': 'Local paper tool protocol check; no model call is made.'})
    workspace = project_dir(project_id) / run.output_path / 'workspace'
    workspace.mkdir(parents=True)
    return run, workspace


def execute_compilation(run):
    from services.api.common import project_dir
    result = subprocess.run([sys.executable, '-m', 'services.worker.execute', '--run-id', run['id']],
                            cwd=ROOT, capture_output=True, text=True, timeout=60)
    receipt = json.loads((project_dir(run['project_id']) / run['output_path'] / 'result.json').read_text())
    assert result.returncode == 0 and receipt['status'] == 'completed', result.stdout + result.stderr + str(receipt)
    return receipt['metrics']


def case_agent_workspace_compile_publishes_actual_bundle_and_exports(client):
    from research.agents.runtime import ToolRuntime
    from services.api.common import project_dir
    from research.paper.manuscript import compile_paper
    pid = project(client)
    previous = ok(client.get('/api/papers/' + pid))
    run, workspace = workspace_agent(pid)
    directory = workspace / 'draft'; directory.mkdir()
    source = document('The actual Agent workspace manuscript. ')
    source = source.replace('\\usepackage{forest_test}', '\\usepackage{forest_test,graphicx}')
    source = source.replace('\\end{document}', '\\includegraphics[width=.2\\linewidth]{figures/plot.pdf}\n\\end{document}')
    (directory / 'main.tex').write_text(source)
    # Real local computation writes the macro consumed by the actual compiler.
    process = subprocess.run([sys.executable, '-c', "from pathlib import Path; Path('results_macros.tex').write_text('\\\\newcommand{\\\\ResultTest}{'+str(sum(range(40)))+'}\\n')"], cwd=directory, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    (directory / 'forest_test.sty').write_text('\\ProvidesPackage{forest_test}\n')
    (directory / 'references.bib').write_text('% Explicit workspace bibliography\n')
    # Compile a real vector asset; it must survive the standalone export.
    plot = directory / 'figures'; plot.mkdir()
    (plot / 'paper.tex').write_text('\\documentclass{article}\n\\begin{document}Actual local PDF asset.\\end{document}\n')
    assert compile_paper(plot)['status'] == 'completed'
    (plot / 'paper.pdf').rename(plot / 'plot.pdf')
    (workspace / 'agent_transcript.json').write_text('{"private_test_material":"must not be copied implicitly"}')
    runtime = ToolRuntime(run.id, workspace)
    arguments = {'source_scope': 'workspace', 'source_path': 'draft/main.tex', 'title': 'Workspace manuscript',
                 'asset_paths': ['draft/forest_test.sty', 'draft/results_macros.tex', 'draft/figures/plot.pdf']}
    submitted = runtime.execute('paper_compile', arguments, 'workspace-compile-action')
    assert submitted['exit_code'] == 0, submitted
    compilation = submitted['run']
    assert runtime.execute('paper_compile', arguments, 'workspace-compile-action')['run']['id'] == compilation['id']
    # The source handoff belongs to the queued compilation, so later unrelated
    # workspace edits do not silently change what this action asked to compile.
    (directory / 'main.tex').write_text('This later edit is not a LaTeX manuscript.')
    receipt = execute_compilation(compilation)
    assert receipt['actual_compilation'] and receipt['source_scope'] == 'workspace'
    assert receipt['origin_agent_run_id'] == run.id and receipt['publication'] == 'applied'
    paper = ok(client.get('/api/papers/' + pid))
    assert paper['revision'] == previous['revision'] + 1 == receipt['paper_revision']
    assert paper['data']['source'] == source
    assert paper['data']['compiled_revision'] == paper['revision']
    assert paper['data']['pdf_path'] == receipt['pdf_path']
    assert paper['data']['source_run_ids'] == [run.id]
    response = client.post('/api/papers/' + pid + '/export', json={'format': 'source'})
    standalone = project_dir(pid).parent / 'workspace-compile-export'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read('paper.tex').decode() == source
        assert 'figures/plot.pdf' in archive.namelist()
        assert 'agent_transcript.json' not in archive.namelist()
        assert 'main.tex' not in archive.namelist()
        archive.extractall(standalone)
    (standalone / 'paper.pdf').unlink()
    assert compile_paper(standalone)['status'] == 'completed'


def case_agent_workspace_compile_protects_edits_and_rejects_wrong_scope(client):
    from research.agents.runtime import ToolRuntime
    from services.api.common import project_dir
    pid = project(client)
    paper = ok(client.get('/api/papers/' + pid))
    owner_source = '\\documentclass{article}\n\\begin{document}Owner manuscript.\\end{document}\n'
    owner = ok(client.patch('/api/papers/' + pid, json={'expected_revision': paper['revision'], 'data': {'source': owner_source}}))
    run, workspace = workspace_agent(pid)
    agent_source = '\\documentclass{article}\n\\begin{document}Agent manuscript.\\end{document}\n'
    (workspace / 'paper.tex').write_text(agent_source)
    runtime = ToolRuntime(run.id, workspace)
    wrong_scope = runtime.execute('paper_compile', {'source_path': 'paper.tex'}, 'wrong-scope')
    assert wrong_scope['exit_code'] == 1 and 'source_scope=workspace' in wrong_scope['error']
    escaping = runtime.execute('paper_compile', {'source_scope': 'workspace', 'source_path': '../paper.tex'}, 'escaping-source')
    assert escaping['exit_code'] == 1
    submitted = runtime.execute('paper_compile', {'source_scope': 'workspace', 'source_path': 'paper.tex'}, 'protect-owner')
    receipt = execute_compilation(submitted['run'])
    assert receipt['publication'] == 'proposed'
    assert ok(client.get('/api/papers/' + pid))['data']['source'] == owner_source
    assert (project_dir(pid) / receipt['paper_source']).read_text() == agent_source
    review = ok(client.get('/api/reviews/' + receipt['review_id']))
    assert review['data']['proposed_data']['source'] == agent_source
    accepted = ok(client.post('/api/reviews/' + receipt['review_id'] + '/apply', json={'expected_revision': owner['revision'], 'indices': [0]}))
    assert accepted['data']['source'] == agent_source and accepted['data']['compiled_revision'] == accepted['revision']
    # Default tool behavior continues compiling the project PaperDocument.
    default = runtime.execute('paper_compile', {}, 'default-project-source')
    assert default['run']['config']['source'] == agent_source
    assert execute_compilation(default['run'])['compiled_revision'] == accepted['revision']


def case_explicit_evidence_scope_never_selects_an_unrequested_run(client):
    from services.api.db import Session, TaskRun, uid
    from services.worker.execute import latest_experiment
    pid = project(client)
    other_pid = project(client)
    with Session.begin() as session:
        runs = [TaskRun(project_id=project_id, request_id=uid(), kind='experiment', status=status,
                        config={'project_goal': 'Preserve authored text'})
                for project_id, status in [(pid, 'completed'), (pid, 'completed'), (other_pid, 'completed'), (pid, 'failed')]]
        session.add_all(runs); session.flush()
        selected = latest_experiment(session, pid, [runs[1].id, runs[0].id])
        assert [run.id for run in selected] == [runs[1].id, runs[0].id]
        for identifiers in ([], '', [runs[0].id, runs[0].id]):
            with pytest.raises(ValueError, match='nonempty list of unique'):
                latest_experiment(session, pid, identifiers)
        with pytest.raises(ValueError, match='Cross-project'):
            latest_experiment(session, pid, [runs[2].id])
        with pytest.raises(ValueError, match='completed runs'):
            latest_experiment(session, pid, [runs[3].id])
        assert len(latest_experiment(session, pid)) == 1


def case_saved_response_rerenders_with_actual_evidence_without_model(client):
    """An authored replay fixture exercises reuse; no model response is faked."""
    from sqlalchemy import select, func
    from services.api.db import Session, TaskRun, SourcePaper, ModelRequest
    from services.api.common import project_dir
    from services.worker.scheduler import enqueue
    from services.worker.execute import saved_paper_response
    from research.paper.evidence import collect_evidence
    from research.paper.model_draft import draft_from_saved_response
    pid = project(client)
    with Session.begin() as session:
        calculation = enqueue(session, pid, 'experiment', {'command': [sys.executable, '-c',
            "import json;json.dump({'integer_sum':sum(range(40))},open('metrics.json','w'))"]})
    calculation_dict = {'id': calculation.id, 'project_id': pid, 'output_path': calculation.output_path}
    actual_metrics = execute_compilation(calculation_dict)
    with Session.begin() as session:
        stored = session.get(TaskRun, calculation.id)
        stored.status = 'completed'; stored.metrics = actual_metrics
        source = SourcePaper(project_id=pid, title='Python sum documentation', data={'abstract': sum.__doc__}, status='available')
        session.add(source); session.flush(); source_id = source.id
        origin = enqueue(session, pid, 'paper_generate', {'run_ids': [calculation.id], 'manuscript_type': 'full_paper'})
        origin.status = 'failed'; origin.error = 'Authored saved-response test record; no provider invocation'
    draft = {'manuscript_type': 'full_paper', 'title': 'An authored arithmetic replay fixture',
             'abstract': 'The computed sum is [[metric:m0]].', 'claim_ids': [],
             'sections': [{'role': role, 'title': role.replace('_', ' ').title(), 'paragraphs': ['A bounded actual calculation.']}
                          for role in ('introduction', 'related_work', 'method', 'experimental_setup', 'results', 'discussion')],
             'conclusion': 'The recorded arithmetic output is reproducible.'}
    draft['sections'][1]['paragraphs'] = ['The builtin sums the supplied sequence. [[source:' + source_id + ']]']
    draft['sections'][2]['blocks'] = [{'type': 'equation', 'latex': r'n>0\Longrightarrow n^2>0'}]
    relative = 'paper/model_responses/authored-fixture/attempt-1.json'
    path = project_dir(pid) / origin.output_path / relative
    path.parent.mkdir(parents=True)
    response = {'text': json.dumps(draft), 'model': 'authored-fixture-not-a-model-call', 'usage': {}}
    path.write_text(json.dumps(response)); original_bytes = path.read_bytes()
    config = {'run_ids': [calculation.id], 'manuscript_type': 'full_paper',
              'saved_response_run_id': origin.id, 'saved_response_path': relative}
    with Session.begin() as session:
        rerender = enqueue(session, pid, 'paper_generate', config)
    receipt = execute_compilation({'id': rerender.id, 'project_id': pid, 'output_path': rerender.output_path})
    assert receipt['draft_reuse']['new_model_requests'] == 0
    assert receipt['draft_reuse']['new_model_cost_usd'] == 0
    assert receipt['draft_reuse']['origin_run_id'] == origin.id
    assert receipt['publication'] == 'applied'
    paper = ok(client.get('/api/papers/' + pid))
    assert r'\Longrightarrow' in paper['data']['source']
    assert paper['data']['draft_reuse']['origin_response_path'] == relative
    assert (project_dir(pid) / paper['data']['pdf_path']).read_bytes().startswith(b'%PDF')
    assert path.read_bytes() == original_bytes
    # A separately attributed scientific edit must produce its own source and
    # PDF, keeping both the origin and a mechanical before/after record.
    revised = json.loads(json.dumps(draft))
    revised['conclusion'] = 'The recorded arithmetic output can be independently recomputed.'
    revision_path = project_dir(pid) / 'paper' / 'revisions' / 'reviewed.json'
    revision_path.parent.mkdir(parents=True, exist_ok=True)
    revision = {'draft': revised, 'editor': 'Local reviewer', 'reason': 'Clarify the bounded arithmetic claim.'}
    revision_path.write_text(json.dumps(revision))
    with Session.begin() as session:
        revision_run = enqueue(session, pid, 'paper_generate', {**config, 'draft_revision_path': 'paper/revisions/reviewed.json'})
    revised_receipt = execute_compilation({'id': revision_run.id, 'project_id': pid, 'output_path': revision_run.output_path})
    assert revised_receipt['publication'] == 'applied'
    provenance = revised_receipt['draft_reuse']
    assert provenance['mode'] == 'saved_response_with_recorded_revision'
    assert provenance['revision']['editor'] == 'Local reviewer'
    assert provenance['revision']['changed_fields'] == 1
    revised_paper = ok(client.get('/api/papers/' + pid))
    assert revised['conclusion'] in revised_paper['data']['source']
    assert revised_paper['data']['draft_reuse'] == provenance
    revised_folder = project_dir(pid) / revision_run.output_path / 'paper'
    assert json.loads((revised_folder / 'draft_revision.json').read_text()) == revision
    assert json.loads((revised_folder / 'draft_revision_changes.json').read_text()) == [
        {'json_pointer': '/conclusion', 'before_exists': True, 'after_exists': True,
         'before': draft['conclusion'], 'after': revised['conclusion']}]
    assert json.loads((revised_folder / 'reused_model_response.json').read_text()) == response
    assert (project_dir(pid) / revised_paper['data']['pdf_path']).read_bytes().startswith(b'%PDF')
    assert path.read_bytes() == original_bytes
    exported = client.post('/api/papers/' + pid + '/export', json={'format': 'source'})
    assert exported.status_code == 200, exported.text
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert archive.read('paper.tex').decode() == revised_paper['data']['source']
        assert archive.read('paper.pdf') == (revised_folder / 'paper.pdf').read_bytes()
        assert json.loads(archive.read('draft_revision.json')) == revision
        assert json.loads(archive.read('draft_reuse.json')) == provenance
        assert json.loads(archive.read('reused_model_response.json')) == response
    with Session() as session:
        assert session.get(TaskRun, origin.id).status == 'failed'
        assert session.scalar(select(func.count()).select_from(ModelRequest)) == 0
        with pytest.raises(ValueError, match='another project'):
            saved_paper_response(session, 'another-project', config, rerender.id)
        for bad_path in ('paper/model_response.json', '../attempt-1.json', '/tmp/attempt-1.json', 'paper/model_responses/../attempt-1.json'):
            with pytest.raises(ValueError, match='Select paper/model_responses'):
                saved_paper_response(session, pid, {**config, 'saved_response_path': bad_path}, rerender.id)
        with pytest.raises(ValueError, match='original explicit ordered'):
            saved_paper_response(session, pid, {**config, 'run_ids': []}, rerender.id)
    # When a generation saved its input context, edited numeric values or row
    # identities cannot be silently reused with the old prose.
    evidence = collect_evidence([{'id': calculation.id, 'status': 'completed',
                                 'directory': str(project_dir(pid) / calculation.output_path)}],
                                [{'id': source_id, 'title': 'Python sum documentation'}])
    old_context = path.parent.parent.parent / 'input_evidence.json'
    old_context.write_text(json.dumps(evidence))
    changed = json.loads(json.dumps(evidence)); changed['runs'][0]['metrics']['integer_sum'] += 1
    with pytest.raises(ValueError, match='values or row identities changed'):
        draft_from_saved_response(path, changed, path.parent / 'rejected', origin_run_id=origin.id,
                                  origin_response_path=relative, expected_type='full_paper', original_evidence_path=old_context)
    revision_path.write_text(json.dumps({'draft': revised, 'editor': '', 'reason': 'Missing editor identity'}))
    with pytest.raises(ValueError, match='requires draft, editor and reason'):
        draft_from_saved_response(path, evidence, path.parent / 'rejected', origin_run_id=origin.id,
                                  origin_response_path=relative, expected_type='full_paper', revision_path=revision_path)


def case_layout_captures_edits_and_rejects_stale_publication(client):
    from services.api.common import project_dir
    pid=project(client)
    paper=ok(client.get('/api/papers/'+pid))
    source='\\documentclass[11pt]{article}\n\\begin{document}\nAuthor sentence stays verbatim.\n\\end{document}\n'
    paper=ok(client.patch('/api/papers/'+paper['id'],json={'expected_revision':paper['revision'],
        'data':{'source':source,'bibtex':'','layout_preflight':{'status':'passed'}}}))
    assert paper['data']['layout_preflight'] is None
    endpoint='/api/papers/'+pid+'/layout'
    assert client.post(endpoint,json={'expected_revision':0,'layout':{}}).status_code==409
    assert client.post(endpoint,json={'expected_revision':paper['revision'],'template':'iclr2027','layout':{'columns':'double'}}).status_code==422
    body={'expected_revision':paper['revision'],'template':'article','layout':{'columns':'double'},'request_id':'actual-layout'}
    run=ok(client.post(endpoint,json=body))
    assert ok(client.post(endpoint,json=body))['id']==run['id']
    inputs=project_dir(pid)/run['config']['compile_input_dir']
    assert (inputs/'paper.tex').read_text()==source
    receipt=execute_compilation(run)
    assert receipt['actual_compilation'] and receipt['publication']=='applied'
    paper=ok(client.get('/api/papers/'+paper['id']))
    assert 'Author sentence stays verbatim.' in paper['data']['source']
    assert 'twocolumn' in paper['data']['source']
    assert paper['data']['manually_edited']
    assert paper['data']['layout_preflight']['actual_compilation']
    run=ok(client.post(endpoint,json={'expected_revision':paper['revision'],'template':'article','layout':{'columns':'single'},'request_id':'layout-race'}))
    newer=source.replace('Author sentence stays verbatim.','A newer author sentence.')
    paper=ok(client.patch('/api/papers/'+paper['id'],json={'expected_revision':paper['revision'],'data':{'source':newer}}))
    receipt=execute_compilation(run)
    assert receipt['publication']=='proposed'
    assert ok(client.get('/api/papers/'+paper['id']))['data']['source']==newer


CASES = [name.removeprefix('case_') for name in list(globals()) if name.startswith('case_')]


@pytest.mark.parametrize('case', CASES)
def test_paper_publication_and_export(tmp_path, case):
    env = {**os.environ, 'FOREST_DATA_DIR': str(tmp_path / 'data'),
           'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'paper.sqlite'),
           'FOREST_OWNER_TOKEN': 'paper-safety-owner-token', 'PYTHONPATH': str(ROOT)}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--case', case],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'SCENARIO PASSED' in result.stdout


if __name__ == '__main__':
    from fastapi.testclient import TestClient
    from services.api.main import app
    selected = sys.argv[2]
    assert selected in CASES
    with TestClient(app) as client:
        globals()['case_' + selected](client)
    print('SCENARIO PASSED:', selected)
