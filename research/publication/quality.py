"""Inspect full-submission evidence; absent measurements never become passes.

This is a delivery/coverage audit. Scientific merit remains the independent
reviewer's evidenced judgment and no machine audit predicts venue acceptance.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import re
from statistics import median
from urllib.parse import urlparse

from .profile import publication_profile


def _path(root, relative):
    root = Path(root).resolve()
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('Use a nonempty relative evidence path')
    value = (root / relative).resolve()
    if not value.is_relative_to(root):
        raise ValueError('Evidence path leaves its project/workspace')
    return value


def _load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _pointer(value,path):
    for key in path.strip('/').split('/'):
        value=value[int(key)] if isinstance(value,list) else value[key.replace('~1','/').replace('~0','~')]
    return value


def _official(url, kind):
    """Require a final proceedings record, or a sourced official decision."""
    parsed = urlparse(url or '')
    if parsed.scheme != 'https':
        return False
    host = (parsed.hostname or '').lower()
    if kind == 'official_decision':
        return host == 'openreview.net' and parsed.path == '/forum' and bool(parsed.query)
    if kind != 'official_proceedings':
        return False
    if host in {'roboticsproceedings.org', 'www.roboticsproceedings.org'}:
        # RSS uses a volume and paper-number landing record, e.g. DROID's
        # /rss20/p120.html. A proceedings index or direct PDF is not that record.
        # URL eligibility does not replace the importer's actual page/title
        # checks or this audit's full-text, passage and peer-analysis evidence.
        return (parsed.netloc.lower() in {host, host + ':443'} and not parsed.params
                and re.fullmatch(r'/rss(?:0[1-9]|[1-9][0-9])/p(?!000)[0-9]{3}\.html', parsed.path) is not None)
    paths = {'proceedings.mlr.press': '.html', 'papers.nips.cc': '/paper',
             'proceedings.neurips.cc': '/paper', 'openaccess.thecvf.com': '.html',
             'proceedings.iclr.cc': '/paper_files/paper/',
             'aclanthology.org': '/', 'dl.acm.org': '/doi/',
             'ieeexplore.ieee.org': '/document/', 'nature.com': '/articles/',
             'www.nature.com': '/articles/', 'science.org': '/doi/', 'www.science.org': '/doi/'}
    marker = paths.get(host)
    return bool(marker and marker in parsed.path and parsed.path.strip('/'))


def _rows(path):
    if path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8', newline='') as stream:
            return list(csv.DictReader(stream))
    if path.suffix.lower() == '.json':
        value = _load(path)
        rows = value if isinstance(value, list) else value.get('observations', value.get('rows'))
        if isinstance(rows, list) and all(isinstance(x, dict) for x in rows):
            return rows
    raise ValueError('Raw observations must be a CSV or a JSON array/rows/observations')


def _main_extent(pages):
    """Count actual main-text pages/words before the reference/appendix header."""
    words, count = 0, 0
    for text in pages:
        boundary=re.search(r'(?:^|\n)\s*(?:References|Bibliography|Appendix|Appendices)\s*(?:\n|$)',text,re.I)
        main=text[:boundary.start()] if boundary else text
        tokens=re.findall(r'\b[\w]+(?:[-’\'][\w]+)*\b',main)
        words+=len(tokens)
        if tokens:count+=1
        if boundary:break
    return {'main_pages':count,'main_words':words}


def _source_extent(source):
    grouped={}
    for passage in source.get('passages',[]):
        page=passage.get('page')
        if isinstance(page,int) and not isinstance(page,bool) and page>0:
            grouped.setdefault(page,[]).append(passage.get('text',''))
    return _main_extent(['\n'.join(grouped[page]) for page in sorted(grouped)])


def assess_submission(root, profile=None, *, sources=(), runs=(), manifest=None):
    """Cross-check editable manifests against actual source, run and file evidence."""
    root = Path(root)
    profile = profile or publication_profile()
    if profile['id'] == 'operational':
        return {'status': 'not_requested', 'ready': True, 'profile': profile, 'gaps': []}
    gaps = []
    def gap(code, finding, role):
        gaps.append({'code': code, 'finding': finding, 'next_role': role})
    run_by_id = {r['id']: r for r in runs}
    source_by_id = {s['id']: s for s in sources}
    manifest_path = None
    if manifest is None:
        candidates = [root / profile['evidence_manifest']]
        candidates.extend(root / r.get('output_path', '') / 'workspace' / profile['evidence_manifest']
                          for r in runs if r.get('status') == 'completed')
        for path in candidates:
            try:
                if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
                    continue
                manifest = _load(path)
                manifest_path = str(path.relative_to(root))
                break
            except (OSError, ValueError, TypeError):
                gap('invalid_manifest', 'The saved submission manifest is unreadable: ' + str(path), 'Experiment Designer')
    if not isinstance(manifest, dict):
        gap('missing_manifest', 'Create an evidence-backed ' + profile['evidence_manifest'] + ' for the full submission.', 'Experiment Designer')
        return {'status': 'needs_revision', 'ready': False, 'profile': profile, 'gaps': gaps, 'counts': {}}
    def array(name):
        value=manifest.get(name,[])
        if not isinstance(value,list):
            gap('invalid_'+name,name+' must be an array of actual evidence records.','Experiment Designer')
            return []
        return value
    for key in ('target_venue', 'central_claim', 'candidate'):
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            gap('missing_' + key, 'State the concrete ' + key + ' in the editable submission protocol.', 'Experiment Designer')

    peers = []
    peer_extents=[]
    seen = set()
    for peer in array('comparable_papers'):
        if not isinstance(peer, dict):
            gap('invalid_peer', 'Comparable-paper analyses must be objects.', 'Benchmark Curator'); continue
        sid = peer.get('source_id')
        source = source_by_id.get(sid, {}).get('data', {})
        passages = {p.get('id'): p for p in source.get('passages', []) if isinstance(p, dict)}
        scope=source.get('read_scope',source.get('reading_scope'))
        valid = sid not in seen and scope in ('full_text','fulltext') and bool(passages)
        valid = valid and _official(peer.get('acceptance_url'), peer.get('acceptance_kind'))
        # The final URL must actually occur in the imported source provenance.
        source_urls = {source.get('url'), source.get('acceptance_url'), source.get('proceedings_url')}
        source_urls.update(p.get('source_url') for p in passages.values())
        valid = valid and peer.get('acceptance_url') in source_urls
        if peer.get('acceptance_kind') == 'official_decision':
            decision = passages.get(peer.get('acceptance_passage_id'), {}).get('text', '').lower()
            valid = valid and ('accept' in decision and 'reject' not in decision)
        for field in ('relevance', 'experiment_design', 'figures', 'tables', 'adopted_changes'):
            detail = peer.get(field)
            valid = valid and isinstance(detail, dict) and bool(detail.get('finding')) and isinstance(detail.get('passage_ids'),list) and bool(detail.get('passage_ids'))
            if isinstance(detail, dict):
                valid = valid and all(ref in passages for ref in detail.get('passage_ids', []))
        scale = peer.get('scale', {})
        unknowns=peer.get('scale_unknowns',{})
        valid=valid and isinstance(scale,dict) and isinstance(unknowns,dict)
        if isinstance(scale,dict) and isinstance(unknowns,dict):
            for dimension in ('datasets','baselines','seeds','independent_units'):
                value=scale.get(dimension)
                if dimension not in scale:
                    valid=False
                elif value is None:
                    note=unknowns.get(dimension)
                    if isinstance(note,str):
                        valid=valid and bool(note.strip()) and bool(peer.get('experiment_design',{}).get('passage_ids'))
                    else:
                        valid=valid and isinstance(note,dict) and bool(note.get('reason')) and bool(note.get('passage_ids')) and all(p in passages for p in note.get('passage_ids',[]))
                else:
                    valid=valid and _number(value) and value>0
        if valid:
            extent=_source_extent(source)
            if not extent['main_words'] or not extent['main_pages']:
                gap('missing_peer_extent','Read page-indexed full text to derive actual manuscript length for '+str(sid)+'.','Benchmark Curator')
                continue
            seen.add(sid); peers.append(peer);peer_extents.append(extent)
        else:
            gap('unverified_peer', 'Read full text and official acceptance evidence, and source each design/visual/scale finding for ' + str(sid) + '.', 'Benchmark Curator')
    if len(peers) < profile['accepted_papers']:
        gap('accepted_corpus', f"Only {len(peers)} usable accepted-paper analyses; require {profile['accepted_papers']} closely related accepted papers.", 'Literature Scout')
    compatible_values={k:[p['scale'][k] for p in peers if _number(p['scale'].get(k)) and p.get('scale_comparability',{}).get(k) is True]
                       for k in ('datasets','baselines','seeds')}
    targets = {k:max(profile[k],math.ceil(median(values))) if len(values)>=3 else profile[k]
               for k,values in compatible_values.items()}
    actual_unit=manifest.get('statistical_unit')
    unit_values=[p['scale']['independent_units'] for p in peers if _number(p['scale'].get('independent_units')) and
                 actual_unit and p['scale'].get('unit_type')==actual_unit and p.get('scale_comparability',{}).get('independent_units') is True]
    unit_target=math.ceil(median(unit_values)) if len(unit_values)>=3 else None
    length_targets={key:math.ceil(median([p[key] for p in peer_extents])) for key in ('main_pages','main_words')} if peer_extents else {}

    def records(name, fields):
        rows = manifest.get(name, [])
        if not isinstance(rows, list):
            gap('invalid_' + name, name + ' must be a list of concrete designs.', 'Experiment Designer'); return []
        valid = []
        ids = set()
        for row in rows:
            if not isinstance(row, dict) or any(not row.get(k) for k in fields) or row.get('id') in ids:
                gap('invalid_' + name, 'Supply distinct ' + name + ' with ' + ', '.join(fields) + '.', 'Experiment Designer')
                continue
            ids.add(row['id']); valid.append(row)
        return valid
    datasets = records('datasets', ('id', 'source', 'split_policy', 'sample_size'))
    baselines = records('baselines', ('id', 'source', 'fair_budget'))
    ablations = records('ablations', ('id', 'mechanism'))
    studies = records('studies', ('id', 'argumentative_duty'))
    seeds = manifest.get('seeds', [])
    if not isinstance(seeds, list) or any(isinstance(s, bool) or not isinstance(s, int) for s in seeds) or len(set(seeds)) != len(seeds):
        gap('invalid_seeds', 'Declare distinct integer seeds/independent replicate IDs.', 'Experiment Designer'); seeds = []
    for name, observed in (('datasets', len(datasets)), ('baselines', len(baselines)), ('seeds', len(seeds))):
        if observed < targets[name]:
            gap('scale_' + name, f"Planned {name}: {observed}; accepted-paper-informed target: {targets[name]}.", 'Experiment Designer')
    if len(ablations) < profile['ablations']:
        gap('scale_ablations', f"Require {profile['ablations']} distinct mechanism ablations.", 'Experiment Designer')
    duties = {x['argumentative_duty'] for x in studies}
    missing_duties = set(profile['experiment_duties']) - duties
    if missing_duties:
        gap('study_coverage', 'Design studies for ' + ', '.join(sorted(missing_duties)) + '.', 'Experiment Designer')
    for study in studies:
        if study['argumentative_duty'] not in profile['experiment_duties']:
            gap('invalid_duty', 'Assign a concrete scientific duty to ' + study['id'] + '.', 'Experiment Designer')

    receipts = {}
    def completed(rid):
        if rid in receipts:
            return receipts[rid]
        run = run_by_id.get(rid, {})
        ok = run.get('status') == 'completed'
        try:
            receipt = _load(_path(root, run.get('output_path', '') + '/result.json'))
            expected = run.get('config', {}).get('execution_attempt', {}).get('id')
            ok = ok and receipt.get('status') == 'completed' and (not expected or receipt.get('attempt_id') == expected)
        except (OSError, ValueError, TypeError):
            ok = False
        receipts[rid] = bool(ok)
        return receipts[rid]
    observed = set()
    measured_runs = set()
    units = {}
    raw_cache = {}
    for cell in array('measured_cells'):
        try:
            rid = cell['run_id']; run = run_by_id.get(rid, {})
            key = (cell['dataset'], cell['method'], cell['seed'], cell['study'])
            if not completed(rid) or run.get('kind') not in ('experiment', 'command', 'agent'):
                raise ValueError('No completed measured execution receipt')
            if run.get('kind') == 'agent' and not any(x.get('status') == 'completed' and x.get('exit_code') == 0 for x in run.get('metrics', {}).get('executions', [])):
                raise ValueError('No successful actual computation in the agent receipt')
            raw = _path(_path(root, run['output_path'] + '/workspace'), cell['raw_path'])
            if raw not in raw_cache:
                raw_cache[raw] = _rows(raw)
            matching = [row for row in raw_cache[raw] if all(str(row.get(name)) == str(value) for name, value in zip(('dataset', 'method', 'seed', 'study'), key))]
            identity = {str(row.get('unit_id', row.get('sample_id'))) for row in matching if row.get('unit_id', row.get('sample_id')) is not None}
            if not matching or len(identity) != len(matching):
                raise ValueError('Missing/duplicate independent observation identities for this measured cell')
            numeric_fields = [name for name in matching[0] if name not in ('dataset', 'method', 'seed', 'study', 'unit_id', 'sample_id')]
            numeric = False
            for name in numeric_fields:
                try:
                    numeric = numeric or all(math.isfinite(float(row[name])) for row in matching)
                except (TypeError, ValueError, KeyError):
                    continue
            if not numeric:
                raise ValueError('No finite numeric observations')
            observed.add(key); measured_runs.add(rid)
            units[key] = len(identity)
        except (KeyError, TypeError, ValueError, OSError) as exc:
            gap('unmeasured_cell', str(exc) + ': ' + str(cell), 'Experimenter')
    methods = {x['id'] for x in baselines} | {manifest.get('candidate')}
    expected_cells=set()
    dataset_ids={d['id'] for d in datasets}
    for study in studies:
        if study['argumentative_duty']=='effectiveness':
            # The primary comparison covers all declared benchmarks/baselines.
            expected_cells|={(d,method,seed,study['id']) for d in dataset_ids for method in methods for seed in seeds}
            continue
        scope=study.get('datasets')
        selected_methods=study.get('methods')
        selected_seeds=study.get('seeds',seeds)
        if not isinstance(scope,list) or not scope or any(not isinstance(x,str) for x in scope) or not set(scope)<=dataset_ids or not study.get('scope_rationale'):
            gap('study_scope','Declare relevant datasets and a scientific scope_rationale for '+study['id']+'; do not multiply unrelated ablations across every benchmark.','Experiment Designer')
            continue
        if not isinstance(selected_methods,list) or not selected_methods or any(not isinstance(x,str) for x in selected_methods) or not set(selected_methods)<=(methods|{a['id'] for a in ablations}):
            gap('study_methods','Declare actual comparator/component method IDs for scoped study '+study['id']+'.','Experiment Designer')
            continue
        if not isinstance(selected_seeds,list) or not selected_seeds or any(isinstance(x,bool) or not isinstance(x,int) for x in selected_seeds) or not set(selected_seeds)<=set(seeds):
            gap('study_seeds','Declare independent replicate IDs from the full protocol for '+study['id']+'.','Experiment Designer')
            continue
        expected_cells|={(d,method,seed,study['id']) for d in scope for method in selected_methods for seed in selected_seeds}
    for ablation in ablations:
        if not any(cell[1]==ablation['id'] for cell in expected_cells):
            gap('unplanned_ablation','Assign mechanism ablation '+ablation['id']+' to its relevant scoped experiment.','Experiment Designer')
    missing_cells = expected_cells - observed
    if not expected_cells or missing_cells:
        gap('incomplete_matrix', f"{len(missing_cells)} of {len(expected_cells)} required baseline/candidate/ablation cells remain unmeasured.", 'Experimenter')
    for study in studies:
        if not any(k[3] == study['id'] for k in observed):
            gap('unexecuted_study', 'No measured evidence for study ' + study['id'] + '.', 'Experimenter')
    sizes = {d['id']: d['sample_size'] for d in datasets}
    undersized = [key for key, count in units.items() if not _number(sizes.get(key[0])) or count < max(sizes.get(key[0], 0), unit_target or 1)]
    if undersized:
        gap('insufficient_real_data', f"{len(undersized)} measured cells have fewer independent units than their dataset protocol/accepted-paper target ({unit_target}).", 'Experimenter')

    analysis_ids = array('analysis_run_ids')
    if any(
        not isinstance(rid, str) or not completed(rid) or rid in measured_runs
        or run_by_id[rid].get('kind') not in ('analysis', 'agent', 'command')
        for rid in analysis_ids
    ) or len(set(analysis_ids)) < 2:
        gap('independent_analysis', 'List at least two distinct completed analysis runs.', 'Analyst')
    stats = array('statistics')
    if not stats or any(not isinstance(s, dict) or s.get('analysis_run_id') not in analysis_ids or
                        not all(_number(s.get(k)) for k in ('effect', 'ci_low', 'ci_high')) or s['ci_low'] > s['ci_high'] for s in stats):
        gap('statistics', 'Record actual independently recomputed effects and uncertainty intervals with their analysis run IDs.', 'Analyst')
    for statistic in stats:
        try:
            run=run_by_id[statistic['analysis_run_id']]
            actual=run.get('metrics',{}).get('observed_metrics',run.get('metrics',{}))
            pointers=statistic.get('pointers',{'effect':'/improvement','ci_low':'/ci_low','ci_high':'/ci_high'})
            for key in ('effect','ci_low','ci_high'):
                if not math.isclose(float(_pointer(actual,pointers[key])),statistic[key],rel_tol=1e-10,abs_tol=1e-12):
                    raise ValueError('Declared statistics do not match the completed analysis measurements')
        except (ValueError,TypeError,KeyError,IndexError) as exc:
            gap('unbound_statistics',str(exc)+': '+str(statistic),'Analyst')
    reviewers = array('reviewer_run_ids')
    if not reviewers or any(not isinstance(rid,str) or not completed(rid) or rid in measured_runs or rid in analysis_ids or run_by_id.get(rid,{}).get('config',{}).get('role') not in ('Reviewer','Submission Reviewer') for rid in reviewers):
        gap('independent_review', 'Complete an independent submission review after measurements, analyses and the compiled draft exist.', 'Submission Reviewer')
    review_records=[]
    for rid in reviewers:
        try:
            review_path=_path(root, run_by_id[rid]['output_path'] + '/workspace/submission_review.json')
            review = _load(review_path)
            if review.get('verdict') != 'ready' or review.get('unresolved_issues') != [] or not review.get('evidence'):
                raise ValueError('Review has unresolved or unevidenced delivery judgments')
            for locator in review['evidence']:
                if isinstance(locator,str):
                    if not _path(root,locator).is_file():raise ValueError('Review refers to an unavailable artifact')
                elif isinstance(locator,dict):
                    source=source_by_id.get(locator.get('source_id'),{}).get('data',{})
                    if not any(p.get('id')==locator.get('passage_id') for p in source.get('passages',[])):
                        raise ValueError('Review refers to an unavailable original source passage')
                else:
                    raise ValueError('Review evidence must name real project-relative files or source_id/passage_id pairs')
            review_records.append((rid,review,review_path))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            gap('submission_review', str(exc) + ': ' + str(rid), 'Submission Reviewer')
    manuscript_extent={}
    paper = manifest.get('manuscript', {})
    if not isinstance(paper,dict):paper={}
    try:
        if not completed(paper.get('compile_run_id')) or run_by_id[paper['compile_run_id']]['kind'] not in ('paper_compile', 'paper_generate'):
            raise ValueError('No actual completed manuscript compilation run')
        source = _path(root, paper['source_path']); pdf = _path(root, paper['pdf_path'])
        compiled_run=run_by_id[paper['compile_run_id']]
        actual_pdf=compiled_run.get('metrics',{}).get('pdf_path')
        actual_source=compiled_run.get('metrics',{}).get('paper_source')
        if not actual_pdf or _path(root,actual_pdf)!=pdf:
            raise ValueError('The manuscript PDF is not the artifact produced by the declared completed compilation')
        if actual_source and _path(root,actual_source)!=source:
            raise ValueError('The manuscript source does not match the declared completed compilation')
        for rid,review,review_path in review_records:
            inspected=review.get('reviewed_run_ids',[])
            required=measured_runs|set(analysis_ids)|{paper['compile_run_id']}
            if not isinstance(inspected,list) or not required<=set(inspected):
                gap('review_coverage','Independent review '+str(rid)+' must inspect every referenced experiment/analysis and the actual final compile run.','Submission Reviewer')
            latest_material=max([source.stat().st_mtime,pdf.stat().st_mtime,*[p.stat().st_mtime for p in raw_cache]])
            if review_path.stat().st_mtime<latest_material:
                gap('stale_submission_review','Independent review '+str(rid)+' predates changed manuscript or measurement artifacts; review the current evidence.','Submission Reviewer')
        layout = _load(_path(root, paper['layout_report']))
        if not source.is_file() or not pdf.read_bytes().startswith(b'%PDF'):
            raise ValueError('Manuscript source/PDF is absent')
        if layout.get('actual_compilation') is not True or layout.get('issues') or layout.get('status') != 'passed':
            raise ValueError('Compiled layout still needs inspection or repairs')
        from pypdf import PdfReader
        manuscript_extent=_main_extent([page.extract_text() or '' for page in PdfReader(str(pdf)).pages])
        for name,target in length_targets.items():
            if manuscript_extent[name]<target:
                gap('manuscript_'+name,f"Actual manuscript {name}: {manuscript_extent[name]}; accepted-paper median: {target}. Expand the substantive methods/evidence within the actual venue limits.",'Writer')
        # Duties and anchors come from the actual structured draft and compiler
        # auxiliary report, rather than an agent's claimed image count.
        draft=_load(source.parent/'draft.json')
        if draft.get('manuscript_type')!='full_paper':
            raise ValueError('A demo or measurement excerpt cannot satisfy full-submission manuscript delivery')
        from research.paper.structure import FULL_PAPER_ROLES
        roles={section.get('role') for section in draft.get('sections',[]) if isinstance(section,dict)}
        if not FULL_PAPER_ROLES<=roles:
            gap('full_paper_sections','The actual full manuscript lacks substantive section roles: '+', '.join(sorted(FULL_PAPER_ROLES-roles))+'.','Writer')
        from research.paper.structure import section_blocks
        visuals=[(section.get('role'),block) for section in draft['sections'] for block in section_blocks(section) if block['type'] in ('table','figure')]
        placements=_load(_path(root,paper.get('placement_report',str((source.parent/'placement_report.json').relative_to(root)))))
        observed_placements=placements.get('placements',[])
        if placements.get('status')!='passed' or not observed_placements:
            gap('visual_placement','Inspect and repair actual compiled figure/table positions relative to their argumentative paragraph anchors.','Layout Reviewer')
        by_label={p.get('label'):p for p in observed_placements}
        for role,block in visuals:
            position=by_label.get(block.get('label'),{})
            if not position.get('has_local_reference') or position.get('page_distance') not in (0,1):
                gap('unverified_visual_anchor','Verify the actual numbered reference and page proximity for '+str(block.get('label'))+'.','Layout Reviewer')
            if block.get('argumentative_duty') not in profile['experiment_duties']:
                gap('visual_duty','Record the scientific argumentative duty of '+str(block.get('label'))+' in its block and adjacent prose.','Writer')
        if not any(role=='results' and block['type']=='table' and block.get('argumentative_duty')=='effectiveness' for role,block in visuals):
            gap('main_results_table','Provide the substantive measured baseline/candidate comparison table in Results with its effectiveness argument.','Writer')
        if not any(role in ('introduction','method') and block['type']=='figure' and block.get('argumentative_duty')=='mechanism' for role,block in visuals):
            gap('mechanism_visual','Provide the necessary method/introductory mechanism diagram next to its substantive explanation.','Figure Designer')
        if not any(role=='results' and block['type']=='figure' for role,block in visuals):
            gap('analysis_visual','Provide an analysis figure that explains the measured results and uncertainty; preserve the full relevant comparator/data coverage.','Figure Designer')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        gap('compiled_manuscript', str(exc), 'Writer')
    return {'status': 'ready' if not gaps else 'needs_revision', 'ready': not gaps,
            'profile': profile, 'manifest_path': manifest_path, 'gaps': gaps,
            'targets': {**targets, 'independent_units': unit_target,**length_targets},
            'comparable_scale_counts':{**{k:len(values) for k,values in compatible_values.items()},'independent_units':len(unit_values)},
            'counts': {'accepted_papers': len(peers), 'datasets': len(datasets), 'baselines': len(baselines),
                       'seeds': len(seeds), 'ablations': len(ablations), 'required_cells': len(expected_cells),
                       'measured_cells': len(observed), 'remaining_cells': len(missing_cells),**manuscript_extent},
            'remaining_cells': [dict(zip(('dataset', 'method', 'seed', 'study'), key)) for key in sorted(missing_cells, key=str)][:100],
            'verification_scope': 'Actual file, source and execution coverage; reviewer judgments require scientific assessment and do not guarantee acceptance.'}
