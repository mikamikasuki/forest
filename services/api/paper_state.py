"""Revision-aware manuscript publication shared by the worker and editor.

Generated bundles remain ordinary files in their run directories. Replacing an edited or newer
working manuscript requires applying a concrete review against its exact revision.
"""
from copy import deepcopy
from difflib import unified_diff
from pathlib import Path
import shutil

from sqlalchemy import select
from sqlalchemy.orm import object_session

from .common import emit, error, get, project_dir, safe_path
from .db import FileRevision, PaperDocument, Review, TaskRun, Project


def enqueue_workspace_compile(session, agent_run_id, workspace, arguments, request_id):
    """Copy explicitly selected Agent files into an editable compilation bundle.

    Compilation never substitutes the project's current manuscript. Publication
    uses the existing exact-revision proposal mechanism after actual success.
    """
    from services.worker.scheduler import enqueue, _lock_project
    origin = get(session, TaskRun, agent_run_id)
    if origin.kind != 'agent':
        raise ValueError('Workspace manuscript submission requires an Agent run')
    _lock_project(session, origin.project_id)
    root = project_dir(origin.project_id)
    workspace = Path(workspace).resolve()
    if workspace != safe_path(root, origin.output_path + '/workspace'):
        raise ValueError('Compile from the current Agent run workspace')
    previous = session.scalar(select(TaskRun).where(TaskRun.project_id == origin.project_id, TaskRun.request_id == request_id))
    if previous:
        if previous.kind != 'paper_compile' or previous.config.get('origin_agent_run_id') != agent_run_id or previous.config.get('origin_source_path') != arguments.get('source_path'):
            raise ValueError('Compilation request ID belongs to another request')
        return previous
    source_path = arguments.get('source_path')
    if not isinstance(source_path, str) or not source_path:
        raise ValueError('Workspace compilation requires source_path')
    source = safe_path(workspace, source_path, True)
    if not source.is_file() or source.suffix.lower() != '.tex':
        raise ValueError('Workspace source_path must identify an actual .tex file')
    source.read_text(encoding='utf-8')
    bundle_root = source.parent
    files = {'paper.tex': source}

    def add_asset(path):
        path = path.resolve()
        if not path.is_relative_to(bundle_root) or not path.is_file():
            raise ValueError('All manuscript assets must be files beneath the source directory')
        relative = path.relative_to(bundle_root).as_posix()
        if relative in files and files[relative] != path:
            raise ValueError('Manuscript assets conflict with a canonical source filename')
        files[relative] = path

    bibliography_path = arguments.get('bibliography_path')
    bibliography = safe_path(workspace, bibliography_path, True) if bibliography_path else bundle_root / 'references.bib'
    if bibliography_path or bibliography.is_file():
        bibliography.read_text(encoding='utf-8')
        add_asset(bibliography)
        if 'references.bib' in files and files['references.bib'] != bibliography:
            raise ValueError('The selected bibliography conflicts with references.bib')
        files['references.bib'] = bibliography
    assets = arguments.get('asset_paths') or []
    if not isinstance(assets, list) or any(not isinstance(path, str) or not path for path in assets):
        raise ValueError('asset_paths must be workspace-relative file or directory paths')
    for relative in assets:
        path = safe_path(workspace, relative, True)
        if not path.is_relative_to(bundle_root):
            raise ValueError('All manuscript assets must be beneath the source directory')
        if path.is_dir():
            for child in sorted(path.rglob('*')):
                if child.is_symlink():
                    raise ValueError('Select regular manuscript assets instead of symlinks')
                if child.is_file():
                    add_asset(child)
        else:
            add_asset(path)
    manifest = [{'workspace_path': str(path.relative_to(workspace)), 'bundle_path': relative,
                 'bytes': path.stat().st_size} for relative, path in sorted(files.items())]
    run = enqueue(session, origin.project_id, 'paper_compile', {
        'source_scope': 'workspace', 'origin_agent_run_id': agent_run_id,
        'origin_source_path': source_path, 'compile_input_files': manifest,
        'title': arguments.get('title') or 'Agent manuscript'}, request_id)
    destination = safe_path(root, run.output_path + '/paper_inputs')
    destination.mkdir(parents=True, exist_ok=True)
    for relative, path in files.items():
        target = safe_path(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    run.config = {**run.config, 'compile_input_dir': str(destination.relative_to(root))}
    return run


def locked_project_paper(session, project_id):
    from services.worker.scheduler import _lock_project
    _lock_project(session, project_id)
    return session.scalar(select(PaperDocument).where(
        PaperDocument.project_id == project_id
    ).with_for_update().execution_options(populate_existing=True))


def locked_paper(session, paper_id):
    paper = get(session, PaperDocument, paper_id)
    current = locked_project_paper(session, paper.project_id)
    if current is None or current.id != paper_id:
        error('REVISION_CONFLICT', 'The working manuscript changed', 409)
    return current


def generation_snapshot(session, project_id):
    paper = locked_project_paper(session, project_id)
    return None if paper is None else {
        'paper_id': paper.id, 'revision': paper.revision,
        'manually_edited': manually_edited(paper),
    }


def manually_edited(paper):
    if 'manually_edited' in paper.data:
        return bool(paper.data['manually_edited'])
    # Older workspaces predate the flag. Compare their editable source with
    # the earlier generated bundle so existing edits receive protection too.
    if paper.data.get('source_dir'):
        folder = safe_path(project_dir(paper.project_id), paper.data['source_dir'])
        for name, field in (('paper.tex', 'source'), ('references.bib', 'bibtex')):
            original = folder / name
            if original.is_file() and original.read_text(encoding='utf-8') != paper.data.get(field, ''):
                return True
    return False


def write_working_source(paper):
    session = object_session(paper)
    if session is None:
        raise ValueError('Working manuscript writes require a database transaction')
    folder = safe_path(project_dir(paper.project_id), 'paper')
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for name, value in (('paper.tex', paper.data.get('source', '')),
                        ('references.bib', paper.data.get('bibtex', ''))):
        path = safe_path(folder, name)
        files.append((name, path, path.with_name(path.name + '.forest-tmp'), value))
    # The two files are one publication: stage both, then swap them in, and put
    # the earlier contents back if any step fails so changed bytes are never
    # left behind an unchanged file revision.
    previous = {path: path.read_bytes() if path.is_file() else None for _, path, _, _ in files}
    try:
        for _, _, temporary, value in files:
            temporary.write_text(value, encoding='utf-8')
        for _, path, temporary, _ in files:
            temporary.replace(path)
    except BaseException:
        for _, path, temporary, _ in files:
            temporary.unlink(missing_ok=True)
            if previous[path] is None:
                path.unlink(missing_ok=True)
            elif not path.is_file() or path.read_bytes() != previous[path]:
                path.write_bytes(previous[path])
        raise
    for name, _, _, _ in files:
        relative = 'paper/' + name
        revision = session.scalar(select(FileRevision).where(
            FileRevision.project_id == paper.project_id, FileRevision.path == relative
        ).with_for_update().execution_options(populate_existing=True))
        if revision is None:
            revision = FileRevision(project_id=paper.project_id, path=relative, revision=0)
            session.add(revision)
        revision.revision += 1
        revision.origin = 'user_edited' if paper.data.get('manually_edited') else 'generated'


def sync_working_file_edit(session, project_id, relative, content):
    field = {'paper/paper.tex': 'source', 'paper/references.bib': 'bibtex'}.get(relative)
    if field is None:
        return
    paper = locked_project_paper(session, project_id)
    if paper is None:
        folder = safe_path(project_dir(project_id), 'paper')
        source = safe_path(folder, 'paper.tex')
        bibliography = safe_path(folder, 'references.bib')
        paper = PaperDocument(project_id=project_id, title='Research manuscript', data={
            'source': source.read_text(encoding='utf-8') if source.exists() else '\\documentclass{article}\n\\begin{document}\n\\end{document}\n',
            'bibtex': bibliography.read_text(encoding='utf-8') if bibliography.exists() else '',
            'bindings': [],
        })
        session.add(paper)
        session.flush()
    paper.data = {**paper.data, field: content, 'manually_edited': True, 'content_origin': 'manual',
                  'layout_preflight': None, 'layout_plan': None}
    paper.revision += 1
    paper.status = 'needs_update'
    emit(session, project_id, 'paper_changed', {'id': paper.id, 'source_path': relative})


def _install_bundle(paper, title, data, *, preserve_manual=False, origin='generated'):
    root = project_dir(paper.project_id)
    source = safe_path(root, data['source_dir'], True)
    destination = safe_path(root, 'paper')
    # Keep the bundle's relative paths intact (macros, styles and figure assets).
    for path in source.rglob('*'):
        if path.is_file() and not path.is_symlink():
            target = safe_path(destination, str(path.relative_to(source)))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    paper.revision = (paper.revision or 0) + 1
    paper.title = title
    paper.data = {**deepcopy(data), 'manually_edited': preserve_manual,
                  'content_origin': origin, 'compiled_revision': paper.revision}
    paper.status = 'ready_for_review'
    write_working_source(paper)


def enqueue_layout(session, paper, layout, template, request_id):
    """Capture the current editable source and assets before a layout job starts."""
    from services.worker.scheduler import enqueue
    previous = session.scalar(select(TaskRun).where(
        TaskRun.project_id == paper.project_id, TaskRun.request_id == request_id)) if request_id else None
    if previous:
        if (previous.kind != 'paper_compile' or previous.config.get('source_scope') != 'layout'
                or previous.config.get('paper_id') != paper.id
                or previous.config.get('layout') != layout or previous.config.get('template') != template):
            error('REQUEST_CONFLICT', 'This request ID belongs to another operation', 409)
        return previous
    run = enqueue(session, paper.project_id, 'paper_compile', {
        'source_scope': 'layout', 'paper_id': paper.id, 'paper_revision': paper.revision,
        'title': paper.title, 'paper_data': deepcopy(paper.data),
        'layout': layout, 'template': template,
    }, request_id)
    root = project_dir(paper.project_id)
    destination = safe_path(root, run.output_path + '/paper_inputs')
    destination.mkdir(parents=True, exist_ok=True)
    source = safe_path(root, 'paper')
    if not source.is_dir() and paper.data.get('source_dir'):
        source = safe_path(root, paper.data['source_dir'], True)
    if source.is_dir():
        for path in source.rglob('*'):
            if path.is_file() and not path.is_symlink():
                target = safe_path(destination, str(path.relative_to(source)))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    for name, field in (('paper.tex', 'source'), ('references.bib', 'bibtex')):
        (destination / name).write_text(paper.data.get(field, ''), encoding='utf-8')
    run.config = {**run.config, 'compile_input_dir': str(destination.relative_to(root))}
    return run


def publish_layout(session, project_id, data, config, run_id):
    paper = locked_paper(session, config['paper_id'])
    if paper.revision == config['paper_revision'] and publication_goal_current(session, project_id, data, run_id):
        _install_bundle(paper, config['title'], data,
                        preserve_manual=manually_edited(paper), origin='layout')
        emit(session, project_id, 'paper_changed', {'id': paper.id, 'run_id': run_id})
        return {'publication': 'applied', 'paper_id': paper.id, 'paper_revision': paper.revision}
    return publish_generation(session, project_id, config['title'], data,
                              {'paper_id': paper.id, 'revision': config['paper_revision']}, run_id)


def publication_goal_current(session, project_id, data, run_id):
    from services.interventions.applicability import goal_applicability
    project = get(session, Project, project_id)
    origin = get(session, TaskRun, run_id)
    if not goal_applicability(session, origin)['ready']: return False
    for identifier in data.get('source_run_ids', []):
        source = session.get(TaskRun, identifier)
        if source is None or source.project_id != project_id or not goal_applicability(session, source)['ready']:
            return False
    return True


def publish_generation(session, project_id, title, data, snapshot, run_id):
    paper = locked_project_paper(session, project_id)
    if paper is None:
        paper = PaperDocument(project_id=project_id, title=title, data={})
        session.add(paper)
        session.flush()
        safe_to_replace = snapshot is None
    else:
        safe_to_replace = (snapshot is not None
                           and snapshot.get('paper_id') == paper.id
                           and snapshot.get('revision') == paper.revision
                           and not manually_edited(paper))
    goal_current = publication_goal_current(session, project_id, data, run_id)
    safe_to_replace = safe_to_replace and goal_current
    if safe_to_replace:
        _install_bundle(paper, title, data)
        emit(session, project_id, 'paper_changed', {'id': paper.id, 'run_id': run_id})
        return {'publication': 'applied', 'paper_id': paper.id,
                'paper_revision': paper.revision}

    reason = ('The generation goal or source evidence no longer matches the current research scope.' if not goal_current
              else 'The current manuscript contains manual edits.' if manually_edited(paper)
              else 'The manuscript changed after this generation was queued.')
    old_source = paper.data.get('source', '')
    new_source = data['source']
    review = Review(project_id=project_id, title='Full Manuscript Proposal — Preserve Existing Edits', status='proposed', data={
        'kind': 'paper_generation', 'origin': 'generated_proposal',
        'summary': reason + ' Review the complete replacement before applying it.',
        'paper_id': paper.id, 'paper_revision': paper.revision,
        'queued_paper_snapshot': deepcopy(snapshot), 'run_id': run_id,
        'goal_applicability_current': goal_current,
        'proposed_title': title, 'proposed_data': deepcopy(data),
        'diff': ''.join(unified_diff(old_source.splitlines(keepends=True),
                                    new_source.splitlines(keepends=True),
                                    fromfile='current/paper.tex', tofile='proposed/paper.tex')),
        'edits': [{'original': old_source, 'replacement': new_source,
                   'reason': reason + ' Accepting replaces the complete manuscript, bibliography and generated assets.'}],
    })
    session.add(review)
    session.flush()
    emit(session, project_id, 'artifact_available', {'kind': 'review', 'id': review.id, 'run_id': run_id})
    return {'publication': 'proposed', 'paper_id': paper.id,
            'paper_revision': paper.revision, 'review_id': review.id}


def apply_generation_review(session, review, paper, indices):
    if indices != [0]:
        error('INVALID_SELECTION', 'Select the complete manuscript replacement to apply this proposal', 422)
    data = review.data
    if not publication_goal_current(session, paper.project_id, data['proposed_data'], data['run_id']):
        error('GOAL_APPLICABILITY_REQUIRED', 'Review the generation and its evidence against the current goal before publishing', 409)
    if data['edits'][0]['original'] != paper.data.get('source', ''):
        error('REVISION_CONFLICT', 'The manuscript source changed; regenerate this proposal', 409)
    _install_bundle(paper, data['proposed_title'], data['proposed_data'])
    paper.data = {**paper.data, 'content_origin': 'accepted_generation', 'accepted_review_id': review.id}
    review.status = 'applied'
    emit(session, paper.project_id, 'paper_changed', {'id': paper.id, 'review_id': review.id})
