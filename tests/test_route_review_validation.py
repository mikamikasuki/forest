"""Pure route-review finding-kind validation and dispatch contracts."""

from copy import deepcopy

import pytest

from research.planning.route_review import (
    route_review_transition,
    validate_route_review,
)

DOCUMENTED_FINDING_KINDS = (
    'goal_drift', 'error_accumulation', 'repeated_failure', 'tunnel_vision',
    'excessive_counterexample_checks', 'other',
)
BLOCKING_FINDING_KINDS = (
    'goal_drift', 'error_accumulation', 'repeated_failure', 'tunnel_vision',
    'excessive_counterexample_checks',
)


def _recommendation():
    return {
        'title': 'Test midpoint quadrature',
        'claim': 'Midpoints reduce error',
        'baseline': 'Left endpoints',
        'experiment': 'Compare observed errors',
        'metric': 'absolute error',
        'experiment_duty': 'mechanism',
        'best_estimate': 'Symmetric local errors cancel',
        'probability_range': [.7, .9],
        'confidence': 'High',
        'why': 'Taylor expansion',
        'against': 'Nonsmooth integrands break the derivation',
        'decisive_unknown': 'Smoothness of the target integrand',
        'cheapest_resolution': 'Check the derivative',
        'evidence_label': 'ESTIMATED',
        'base_case': {
            'most_likely_outcome': 'Lower error',
            'current_best_estimate': 'Second-order convergence',
            'pre_experiment_bet': 'Midpoints win for smooth functions',
            'probability_any_signal': [.7, .9],
            'probability_meaningful_improvement': [.6, .8],
            'probability_publishable_finding': [.01, .05],
            'biggest_reason_to_work': 'Symmetry',
            'biggest_reason_to_fail': 'Nonsmoothness',
            'first_experiment': 'Evaluate the actual integrand',
        },
        'research_question': 'How does smoothness change midpoint accuracy?',
        'success_event': 'Lower absolute integration error at equal evaluations.',
    }


def _context(*, route_replan_required=False):
    return {
        'coverage': {'node_ids': ['editable-method'], 'run_ids': [], 'resource_ids': []},
        'route_health': {'replan_required': route_replan_required},
    }


def _report(decision='replan', kind='other'):
    return {
        'decision': decision,
        'summary': 'Current route judgment',
        'coverage': {'node_ids': ['editable-method'], 'run_ids': [], 'resource_ids': []},
        'findings': [{
            'kind': kind,
            'finding': 'A concrete route observation for the next decision.',
            'node_ids': ['editable-method'],
            'run_ids': [],
        }],
        'stop_node_ids': ['editable-method'],
        'recommendation': _recommendation(),
        'external_input_request': 'Provide the missing held-out labels before choosing the next route.',
    }


@pytest.mark.parametrize('kind', DOCUMENTED_FINDING_KINDS)
def test_every_documented_finding_kind_validates_without_mutating_input(kind):
    context = _context()
    report = _report(kind=kind)
    original_report = deepcopy(report)
    original_context = deepcopy(context)

    validated = validate_route_review(report, context)

    assert validated['findings'][0]['kind'] == kind
    assert report == original_report
    assert context == original_context


@pytest.mark.parametrize('invalid_kind,missing', [
    (None, False), (None, True), ('', False), (' ', False), ('goal_drit', False),
    ('Goal_drift', False), ([], False), ({}, False), (1, False), (True, False),
])
@pytest.mark.parametrize('decision', ['continue', 'replan', 'external_input'])
def test_missing_unknown_blank_case_and_malformed_kinds_fail_before_classification(invalid_kind, missing, decision):
    context = _context()
    report = _report(decision=decision, kind=invalid_kind)
    if missing:
        report['findings'][0].pop('kind')
    original = deepcopy(report)

    with pytest.raises(ValueError, match='recognized finding kinds'):
        validate_route_review(report, context)

    assert report == original


@pytest.mark.parametrize('kind', BLOCKING_FINDING_KINDS)
def test_blocking_finding_requires_replan_and_transition_preserves_control(kind):
    context = _context()
    report = _report(decision='continue', kind=kind)
    with pytest.raises(ValueError, match='route defects require a new direction'):
        validate_route_review(report, context)

    report['decision'] = 'replan'
    validated = validate_route_review(report, context)
    control = {'status': 'running', 'phase': 'ROUTE_REVIEW', 'replan_required': True,
               'replan_reason': 'Periodic review pending'}
    original_control = deepcopy(control)
    transitioned = route_review_transition(control, validated, True)
    assert transitioned['phase'] == 'PLAN'
    assert transitioned['replan_required'] is True
    assert transitioned['route_replan_node_ids'] == ['editable-method']
    assert control == original_control


@pytest.mark.parametrize('findings', [[], [_report(kind='other')['findings'][0]]])
def test_clean_or_other_continue_can_execute_when_route_health_is_healthy(findings):
    context = _context()
    report = _report(decision='continue')
    report['findings'] = findings
    validated = validate_route_review(report, context)
    control = {'status': 'running', 'phase': 'ROUTE_REVIEW', 'replan_required': True,
               'replan_reason': 'Periodic review pending'}

    transitioned = route_review_transition(control, validated, True)

    assert transitioned['phase'] == 'EXECUTE'
    assert transitioned['replan_required'] is False


def test_route_health_still_blocks_clean_continue_and_external_input_waits():
    blocked_context = _context(route_replan_required=True)
    clean = _report(decision='continue')
    clean['findings'] = []
    with pytest.raises(ValueError, match='route defects require a new direction'):
        validate_route_review(clean, blocked_context)

    external = validate_route_review(_report(decision='external_input', kind='other'), _context())
    control = {'status': 'running', 'phase': 'ROUTE_REVIEW', 'replan_required': True}
    transitioned = route_review_transition(control, external, True)
    assert transitioned['status'] == 'waiting_input'
    assert transitioned['phase'] == 'ROUTE_REVIEW'
    assert transitioned['replan_required'] is True
    assert transitioned['reason'] == external['external_input_request']


def test_transition_rejects_unknown_finding_kind_before_controller_advice():
    report = _report(decision='continue', kind='goal_drit')
    control = {'status': 'running', 'phase': 'ROUTE_REVIEW', 'replan_required': True}

    with pytest.raises(ValueError, match='recognized finding kinds'):
        route_review_transition(control, report, True)

    assert control['phase'] == 'ROUTE_REVIEW'
    assert control['replan_required'] is True
