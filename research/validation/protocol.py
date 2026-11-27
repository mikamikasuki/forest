"""Explicit research judgments and protocols, independent of model/provider code.

Validation reports structure and provenance; it never invents a scientific verdict.
All records remain ordinary editable dictionaries.
"""
from __future__ import annotations

import math
import re
from copy import deepcopy

LABELS = {'MEASURED', 'REPORTED', 'INFERRED', 'ESTIMATED', 'SPECULATIVE'}
DUTIES = {'effectiveness', 'mechanism', 'scenario_value', 'alternative_explanation'}
JUDGMENT_FIELDS = ('best_estimate', 'confidence', 'why', 'against', 'decisive_unknown', 'cheapest_resolution')
BASE_CASE_TEXT = ('most_likely_outcome', 'current_best_estimate', 'pre_experiment_bet',
                  'biggest_reason_to_work', 'biggest_reason_to_fail', 'first_experiment')
BASE_CASE_PROBABILITIES = ('probability_any_signal', 'probability_meaningful_improvement',
                           'probability_publishable_finding')


def probability_range(value, field='probability_range'):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f'{field} must contain a lower and upper probability')
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in value):
        raise ValueError(f'{field} must contain finite numbers')
    if not 0 <= value[0] < value[1] <= 1:
        raise ValueError(f'{field} must be a nonzero-width interval within [0, 1]')
    return list(value)


def _text(record, fields, prefix=''):
    for field in fields:
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ValueError(f'{prefix}{field} must state a concrete judgment or explicitly identify missing evidence')


def validate_idea(idea):
    """Validate idea judgments and align the main probability with meaningful improvement."""
    if not isinstance(idea, dict):
        raise ValueError('A research idea must be an object')
    result = deepcopy(idea)
    _text(result, JUDGMENT_FIELDS + ('title', 'claim', 'baseline', 'experiment', 'metric'))
    if result['confidence'] not in {'High', 'Medium', 'Low'}:
        raise ValueError('confidence must be High, Medium or Low')
    if result.get('evidence_label') != 'ESTIMATED':
        raise ValueError('Idea judgments and probability ranges must be explicitly labeled ESTIMATED')
    result['probability_range'] = probability_range(result.get('probability_range'))
    if result.get('experiment_duty') not in DUTIES:
        raise ValueError('Every proposed experiment requires an explicit argumentative duty')
    base = result.get('base_case')
    if not isinstance(base, dict):
        raise ValueError('base_case must include all nine required conclusions')
    _text(base, BASE_CASE_TEXT, 'base_case.')
    for field in BASE_CASE_PROBABILITIES:
        base[field] = probability_range(base.get(field), 'base_case.' + field)
    if base['probability_meaningful_improvement'][0] > base['probability_any_signal'][1]:
        raise ValueError('A meaningful positive improvement cannot be more probable than any positive signal')
    result['probability_range'] = list(base['probability_meaningful_improvement'])
    result['probability_interpretation'] = 'Subjective forecast, not a measured frequency or conference acceptance prediction'
    return result


def distinct_idea_directions(ideas):
    """Drop generated cards that describe the same core experimental design."""
    facets = ('population', 'intervention', 'comparator', 'setting')
    stop = {'a', 'an', 'and', 'as', 'at', 'by', 'for', 'from', 'in', 'into',
            'of', 'on', 'or', 'the', 'to', 'using', 'with'}

    def tokens(value):
        if not isinstance(value, str):
            return set()
        return {word for word in re.findall(r'[^\W_]+', value.casefold()) if word not in stop}

    def same_facet(left, right):
        a, b = tokens(left), tokens(right)
        # A strict superset can add a meaningful design qualifier.
        if a < b or b < a:
            return False
        return bool(a and b and len(a & b) / min(len(a), len(b)) >= .8)

    unique = []
    for idea in ideas:
        design = idea.get('study_design')
        if isinstance(design, dict) and all(isinstance(design.get(key), str) and design[key].strip() for key in facets):
            duplicate = any(
                isinstance(other.get('study_design'), dict)
                and all(same_facet(design[key], other['study_design'].get(key)) for key in facets)
                for other in unique
            )
        else:
            # A generated direction without comparable design facets cannot
            # establish that it is a distinct study, so leave it out.
            duplicate = True
        if not duplicate:
            unique.append(idea)
    return unique, len(ideas) - len(unique)


def validate_experiment(plan):
    """A modifiable protocol; changes never prevent editing or exploration."""
    result = deepcopy(plan)
    _text(result, ('research_question', 'hypothesis', 'baseline', 'candidate', 'metric',
                   'statistical_unit', 'split_policy', 'selection_policy', 'decision_rule'))
    if result.get('argumentative_duty') not in DUTIES:
        raise ValueError('argumentative_duty must be effectiveness, mechanism, scenario_value or alternative_explanation')
    threshold = result.get('meaningful_effect')
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or threshold < 0:
        raise ValueError('meaningful_effect must be an explicit nonnegative finite threshold')
    if result.get('phase') not in {'exploratory', 'confirmatory'}:
        raise ValueError('phase must explicitly distinguish exploratory and confirmatory analysis')
    if result['phase'] == 'confirmatory' and result.get('test_used_for_selection') is not False:
        raise ValueError('Confirmatory analysis must explicitly state that its evaluation data was not used for selection')
    result['editing_policy'] = 'Editable; record changed decisions and their affected evidence'
    return result


def validate_claims(claims, evidence_ids):
    """Check evidence references, never label entailment as mechanically verified."""
    available = set(evidence_ids)
    for claim in claims:
        _text(claim, ('id', 'statement', 'scope'))
        if claim.get('evidence_label') not in LABELS:
            raise ValueError(f"Claim {claim['id']} requires an evidence label")
        if not claim.get('supporting_evidence'):
            raise ValueError(f"Claim {claim['id']} has no supporting evidence")
        for ref in claim.get('supporting_evidence', []) + claim.get('contrary_evidence', []):
            if ref not in available:
                raise ValueError(f"Claim {claim['id']} refers to unavailable evidence {ref}")
    return deepcopy(claims)


def validate_submission_design(plan, profile=None):
    """Validate the full design before execution, without inventing measurements."""
    from research.publication.profile import publication_profile
    profile=profile or publication_profile()
    result=deepcopy(plan)
    if profile['id']=='operational':return result
    _text(result,('target_venue','dataset_scale_rationale','baseline_selection_rationale',
                  'replicate_justification','fair_compute_policy','leakage_checks',
                  'uncertainty_analysis','multiplicity_policy','confirmation_policy'))
    for field in ('datasets','baselines','ablations'):
        rows=result.get(field)
        if not isinstance(rows,list) or len(rows)<profile[field]:
            raise ValueError(f"Full submission requires at least {profile[field]} concrete {field}; a pilot cannot replace this design")
        ids=[row.get('id') for row in rows if isinstance(row,dict)]
        if len(ids)!=len(rows) or any(not isinstance(i,str) or not i.strip() for i in ids) or len(set(ids))!=len(ids):
            raise ValueError(field+' must have distinct concrete IDs')
    seeds=result.get('seeds')
    if not isinstance(seeds,list) or len(seeds)<profile['seeds'] or any(isinstance(x,bool) or not isinstance(x,int) for x in seeds) or len(set(seeds))!=len(seeds):
        raise ValueError(f"Full submission requires {profile['seeds']} distinct seeds/replicate IDs justified by uncertainty")
    studies=result.get('studies')
    if not isinstance(studies,list) or any(not isinstance(x,dict) for x in studies):
        raise ValueError('Design executable effectiveness, mechanism, scenario and alternative-explanation studies')
    if not DUTIES<={x.get('argumentative_duty') for x in studies}:
        raise ValueError('Full submission must cover all four experimental argumentative duties')
    papers=result.get('accepted_source_ids')
    if not isinstance(papers,list) or any(not isinstance(x,str) or not x for x in papers) or len(set(papers))<profile['accepted_papers']:
        raise ValueError(f"Design must cite {profile['accepted_papers']} distinct accepted-paper source IDs; verify full text and acceptance in the delivery audit")
    result['publication_profile']=profile
    result['completion_policy']='Full matrix, raw data, independent statistical review and compiled manuscript required; budget exhaustion preserves unfinished work'
    return result
