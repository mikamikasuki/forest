"""Check the design filter used before persisting generated ideas."""
import pytest

from research.validation.protocol import distinct_idea_directions


@pytest.mark.parametrize('reverse', [False, True])
def test_retains_nested_design_with_an_added_qualifier(reverse):
    broad = {
        'title': 'Compare cache replacement policies',
        'study_design': {
            'population': 'read requests',
            'intervention': 'adaptive cache replacement policy tuned for response latency',
            'comparator': 'least recently used replacement',
            'setting': 'replayed application traces',
        },
    }
    nested = {
        'title': 'Compare cache replacement policies with prefetching',
        'study_design': {
            **broad['study_design'],
            'intervention': broad['study_design']['intervention'] + ' with prefetching',
        },
    }
    ideas = [nested, broad] if reverse else [broad, nested]

    assert distinct_idea_directions(ideas) == (ideas, 0)


def test_still_drops_a_duplicate_design_after_normalization():
    first = {
        'title': 'Compare cache replacement policies',
        'study_design': {
            'population': 'read requests',
            'intervention': 'adaptive cache replacement',
            'comparator': 'least-recently-used replacement',
            'setting': 'replayed application traces',
        },
    }
    duplicate = {
        'title': 'Evaluate an adaptive policy',
        'study_design': {
            **first['study_design'],
            'intervention': 'An Adaptive Cache Replacement',
            'comparator': 'least recently used replacement',
        },
    }

    assert distinct_idea_directions([first, duplicate]) == ([first], 1)
