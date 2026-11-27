"""Real-provider drafting with retained responses and explicit validation repair."""
from __future__ import annotations

import json
from pathlib import Path
import uuid

from research.paper.evidence import manuscript_prompt, validate_draft


def _required_figures(required_figure_ids, evidence):
    if required_figure_ids is None:
        return None
    if not isinstance(required_figure_ids, (list, tuple)) or any(not isinstance(identifier, str) or not identifier.strip() for identifier in required_figure_ids) or len(set(required_figure_ids)) != len(required_figure_ids):
        raise ValueError('Required figure IDs must be a sequence of unique actual figure identifiers')
    missing = set(required_figure_ids) - {figure['id'] for figure in evidence.get('figures', [])}
    if missing:
        raise ValueError('Explicitly selected figures are unavailable in the supplied evidence: ' + ', '.join(sorted(missing)))
    return list(required_figure_ids)


def validate_manuscript_contract(draft, evidence, expected_type=None, *, required_figure_ids=None):
    """One final-output contract for fresh responses and recorded-response reuse.

    Recorded author revisions can repair an earlier response; the final authored
    manuscript still includes every selected asset and omits AI boilerplate.
    """
    required = _required_figures(required_figure_ids, evidence)
    from research.paper.tables import materialize_statistical_tables
    draft = materialize_statistical_tables(draft, evidence)
    validation = validate_draft(draft, evidence, expected_type)
    if required is not None:
        missing = set(required) - set(validation['referenced_figures'])
        if missing:
            raise ValueError('Insert every explicitly selected figure at its scientific argument: ' + ', '.join(sorted(missing)))
        validation['required_figure_ids'] = required
    from research.paper.structure import validate_structure
    from research.paper.style import ai_declarations
    declarations = ai_declarations('\n'.join([draft['title'], draft['abstract'], draft['conclusion'], *validate_structure(draft, evidence)[0]]))
    if declarations:
        raise ValueError('Remove unsolicited AI-writing declarations from manuscript prose; keep execution provenance in research records: ' + '; '.join(declarations))
    return validation


def draft_changes(before, after):
    """Record actual changed JSON fields without attributing edits to a model."""
    changes = []
    def visit(left, right, pointer='', left_exists=True, right_exists=True):
        if left_exists and right_exists and left == right:
            return
        if left_exists and right_exists and isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                visit(left.get(key), right.get(key), pointer+'/'+str(key).replace('~','~0').replace('/','~1'), key in left, key in right)
        elif left_exists and right_exists and isinstance(left, list) and isinstance(right, list):
            for index in range(max(len(left), len(right))):
                visit(left[index] if index < len(left) else None, right[index] if index < len(right) else None,
                      pointer+'/'+str(index), index < len(left), index < len(right))
        else:
            changes.append({'json_pointer': pointer, 'before_exists': left_exists, 'after_exists': right_exists,
                            **({'before': left} if left_exists else {}), **({'after': right} if right_exists else {})})
    visit(before, after)
    return changes


def draft_from_saved_response(response_path, evidence, output_dir, *, origin_run_id,
                              origin_response_path, expected_type, original_evidence_path=None,
                              revision_path=None, revision_project_path=None, required_figure_ids=None):
    """Validate an explicitly selected saved response without invoking a model."""
    if expected_type not in ('full_paper', 'research_note'):
        raise ValueError('Saved-response reuse requires an explicit manuscript_type')
    response_path = Path(response_path)
    response = json.loads(response_path.read_text(encoding='utf-8'))
    if not isinstance(response, dict) or not isinstance(response.get('text'), str) or not isinstance(response.get('model'), str) or not isinstance(response.get('usage'), dict):
        raise ValueError('Saved response must contain recorded text, model and usage fields')
    draft = json.loads(response['text'])
    if not isinstance(draft, dict):
        raise ValueError('Saved response text must contain a manuscript object')
    original_context_available = original_evidence_path is not None and Path(original_evidence_path).is_file()
    if original_context_available:
        original = json.loads(Path(original_evidence_path).read_text())
        # Preserve both numeric identity/value and string row context. Ordinary
        # files stay editable; changed evidence calls for a new authoring pass.
        original_metrics = [(r['id'], r['metrics']) for r in original.get('runs', [])]
        current_metrics = [(r['id'], r['metrics']) for r in evidence['runs']]
        if original_metrics != current_metrics:
            raise ValueError('Saved response evidence values or row identities changed; request a fresh draft for the new evidence')
    revision = None
    changes = []
    if revision_path is not None:
        revision = json.loads(Path(revision_path).read_text(encoding='utf-8'))
        if not isinstance(revision, dict) or not isinstance(revision.get('draft'), dict) or any(not isinstance(revision.get(key), str) or not revision[key].strip() for key in ('editor','reason')):
            raise ValueError('Draft revision requires draft, editor and reason fields')
        changes = draft_changes(draft, revision['draft'])
        draft = revision['draft']
    from research.paper.tables import materialize_statistical_tables
    draft = materialize_statistical_tables(draft, evidence)
    validation = validate_manuscript_contract(draft, evidence, expected_type, required_figure_ids=required_figure_ids)
    if original_context_available:
        original_sources = {source['id']: source for source in original.get('sources', [])}
        current_sources = {source['id']: source for source in evidence['sources']}
        # The validated source IDs include the parent sources of cited passages.
        changed_sources = [key for key in validation['referenced_sources']
                           if key in original_sources and original_sources[key] != current_sources[key]]
        if changed_sources:
            raise ValueError('Saved response cited source evidence changed (' + ', '.join(changed_sources)
                             + '); request a fresh draft for the new evidence')
    provenance = {'mode': 'saved_response_revalidation', 'origin_run_id': origin_run_id,
                  'origin_response_path': origin_response_path, 'original_model': response['model'],
                  'original_usage': response['usage'], 'original_request_id': response.get('request_id'),
                  'original_response_id': response.get('response_id'), 'new_model_requests': 0,
                  'new_model_cost_usd': 0, 'requested_manuscript_type': expected_type,
                  'evidence_run_ids': [run['id'] for run in evidence['runs']],
                  'original_evidence_context_available': original_context_available,
                  'evidence_validation': 'Original and current metric values, row identities, and recorded cited sources agree' if original_context_available else 'Current evidence revalidated; original ordered run selection retained; original evidence context was not saved',
                  'validation': validation}
    if revision is not None:
        provenance['mode'] = 'saved_response_with_recorded_revision'
        provenance['revision'] = {'project_path': revision_project_path, 'editor': revision['editor'],
                                  'reason': revision['reason'], 'changed_fields': len(changes),
                                  'changes_artifact': 'draft_revision_changes.json',
                                  'authorship': 'Recorded revision of saved model prose; not a new model response'}
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'reused_model_response.json').write_text(json.dumps(response, ensure_ascii=False, indent=2))
    if revision is not None:
        (output / 'draft_revision.json').write_text(json.dumps(revision, ensure_ascii=False, indent=2))
        (output / 'draft_revision_changes.json').write_text(json.dumps(changes, ensure_ascii=False, indent=2))
    (output / 'draft_reuse.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2))
    (output / 'generation_attempts.json').write_text(json.dumps({'status': 'validated_saved_response',
        'requested_manuscript_type': expected_type, 'attempts': [], 'reuse': provenance}, ensure_ascii=False, indent=2))
    return draft, response, provenance


def draft_with_model(client, evidence, goal, output_dir, *, title=None, attempts=3, system='', expected_type='full_paper', layout=None, publication=None, required_figure_ids=None):
    """Request at most the configured number of actual model responses.

    Syntax and reference failures return exact feedback to the connected model.
    No prose or result is substituted when the provider cannot produce a valid
    draft. The caller remains responsible for scientific interpretation review.
    """
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise ValueError('paper_draft_attempts must be a positive integer')
    if expected_type not in (None, 'full_paper', 'research_note'):
        raise ValueError('Requested manuscript type must be full_paper or research_note')
    required_figure_ids = _required_figures(required_figure_ids, evidence)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'input_evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    generation_id = str(uuid.uuid4())
    response_dir = output / 'model_responses' / generation_id
    response_dir.mkdir(parents=True)
    (response_dir / 'input_evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    from research.agents.policy import assemble_agent_instructions
    system = assemble_agent_instructions('Writer', {'authoring_mode': 'manuscript'}, publication) + '\n' + system
    if publication is not None:
        from research.publication.profile import publication_instructions
        system += '\n' + publication_instructions(publication)
    if required_figure_ids is not None:
        system += '\nInsert every explicitly selected actual figure into the manuscript with its argumentative duty and real paragraph anchor: ' + json.dumps(required_figure_ids)
    initial = [{'role': 'system', 'content': system},
               {'role': 'user', 'content': manuscript_prompt(evidence, goal, title, expected_type, layout)}]
    messages = list(initial)
    records = []

    def record(item):
        records.append(item)
        status = ('validated' if item.get('valid') else 'provider_error' if item.get('provider_error')
                  else 'failed_validation' if item['attempt'] >= attempts else 'needs_repair')
        report = json.dumps({'generation_id': generation_id, 'configured_attempts': attempts,
                            'requested_manuscript_type': expected_type,
                            'attempts': records, 'status': status,
                            'verification_scope': 'Actual provider response, JSON structure and evidence references; semantic scientific review remains separate'}, indent=2)
        (response_dir / 'generation_attempts.json').write_text(report)
        (output / 'generation_attempts.json').write_text(report)

    for number in range(1, attempts + 1):
        try:
            from research.planning.model import context_model_json
            # Full manuscript evidence commonly exceeds a small planning page.
            # This editable working preference preserves the complete request;
            # an actual provider context rejection still enters paged recovery.
            preference=int(client.config.get('manuscript_context_chars',256000))
            _, response = context_model_json(client, messages, response_dir / 'context_sessions',
                                              char_hint=preference, parse_result=False, output_stage='paper')
        except Exception as exc:
            record({'attempt': number, 'valid': False, 'provider_error': type(exc).__name__, 'error': str(exc)})
            raise
        response_path = response_dir / f'attempt-{number}.json'
        response_path.write_text(json.dumps(response, ensure_ascii=False, indent=2))
        entry = {'attempt': number, 'response_path': str(response_path.relative_to(output)),
                 'model': response.get('model'), 'usage': response.get('usage'), 'elapsed_seconds': response.get('elapsed')}
        try:
            draft = json.loads(response['text'])
            if not isinstance(draft, dict):
                raise ValueError('The manuscript must be a JSON object with title, abstract, sections, conclusion, and claim_ids')
            from research.paper.tables import materialize_statistical_tables
            draft = materialize_statistical_tables(draft, evidence)
            validation = validate_manuscript_contract(draft, evidence, expected_type, required_figure_ids=required_figure_ids)
            from research.paper.statistical_narrative import review_statistical_explanations
            draft, statistical_review = review_statistical_explanations(client, evidence, draft)
            validation = validate_manuscript_contract(draft, evidence, expected_type, required_figure_ids=required_figure_ids)
            if statistical_review.get('status') != 'not_applicable':
                validation['statistical_explanation_review'] = statistical_review
        except (ValueError, KeyError, TypeError) as exc:
            record({**entry, 'valid': False, 'validation_error': str(exc)})
            if number == attempts:
                raise ValueError(f'The actual model returned no valid manuscript after {attempts} responses: {exc}. Inspect generation_attempts.json and model_responses.') from exc
            feedback = ('Correct your actual draft and return the complete JSON document only. Validation error: ' + str(exc) +
                        '\nUse these exact literal numeric reference tokens instead of measured numeric literals: ' +
                        ' '.join('[[metric:' + metric['id'] + ']]' for metric in evidence['metrics']) +
                        '\nclaim_ids must equal ' + json.dumps([claim['id'] for claim in evidence.get('claims', [])]) +
                        '. Metric IDs are not claim IDs. Preserve valid content and its evidence; never invent new scientific results or references.')
            messages = [initial[0], {'role': 'user', 'content': json.dumps({
                'original_task': initial[1]['content'], 'required_correction': feedback,
                'previous_draft_untrusted_model_output': response['text'],
            }, ensure_ascii=False)}]
            continue
        record({**entry, 'valid': True, 'validation': validation})
        (output / 'model_response.json').write_text(json.dumps(response, ensure_ascii=False, indent=2))
        return draft, response
    raise RuntimeError('No model response was requested')
