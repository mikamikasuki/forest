import pytest

from services.observation.segments import index


def markdown_headings(text, path='notes.md'):
    raw = text.encode('utf-8')
    segments, state = index(raw, path)
    assert state == 'parsed'
    return raw, [segment for segment in segments if segment['kind'] == 'heading']


def test_long_backtick_fence_does_not_hide_real_heading_after_inner_short_fence():
    text = (
        '````python\n'
        '```\n'
        '# heading in an example\n'
        '````\n'
        '# Actual heading\n'
        'body\n'
    )

    raw, headings = markdown_headings(text)

    assert [heading['name'] for heading in headings] == ['Actual heading [1]']
    actual = headings[0]
    assert raw[actual['start_byte']:actual['end_byte']] == b'# Actual heading\nbody\n'


def test_tilde_fence_requires_matching_marker_length_and_allows_longer_close():
    text = (
        '~~~~markdown\n'
        '# hidden heading\n'
        '~~~\n'
        '~~~~~\n'
        '# Actual heading\n'
    )

    _, headings = markdown_headings(text, 'notes.markdown')

    assert [heading['name'] for heading in headings] == ['Actual heading [1]']


@pytest.mark.parametrize('bad_closer', ['```', '```` trailing text'])
def test_shorter_or_trailing_text_closers_stay_inside_fence(bad_closer):
    text = (
        '````\n'
        f'{bad_closer}\n'
        '# still hidden\n'
        '````\n'
        '# Actual heading\n'
    )

    _, headings = markdown_headings(text)

    assert [heading['name'] for heading in headings] == ['Actual heading [1]']


def test_other_marker_does_not_close_fence():
    text = '````\n~~~\n# still hidden\n````\n# Actual heading\n'

    _, headings = markdown_headings(text)

    assert [heading['name'] for heading in headings] == ['Actual heading [1]']


def test_fence_indentation_accepts_three_spaces_but_not_four():
    text = (
        '   ```\n'
        '# hidden by three-space fence\n'
        '   ```   \n'
        '# Visible heading\n'
        '    ```\n'
        '# Heading after four-space non-fence\n'
    )

    _, headings = markdown_headings(text)

    assert [heading['name'] for heading in headings] == [
        'Visible heading [1]', 'Heading after four-space non-fence [1]'
    ]


def test_backtick_in_backtick_fence_info_does_not_open_fence():
    _, headings = markdown_headings('```python`not-a-fence\n# Visible heading\n')

    assert [heading['name'] for heading in headings] == ['Visible heading [1]']


def test_unicode_crlf_heading_ranges_remain_utf8_byte_ranges():
    text = '# Café\r\nintro\r\n## Résumé\r\nbody\r\n# Fin\r\n'
    raw, headings = markdown_headings(text)

    assert [heading['name'] for heading in headings] == [
        'Café [1]', 'Café / Résumé [1]', 'Fin [1]'
    ]
    assert raw[headings[0]['start_byte']:headings[0]['end_byte']] == '# Café\r\nintro\r\n## Résumé\r\nbody\r\n'.encode()
    assert raw[headings[1]['start_byte']:headings[1]['end_byte']] == '## Résumé\r\nbody\r\n'.encode()
    assert headings[0]['start_line'] == 1 and headings[0]['end_line'] == 4
    assert headings[1]['start_line'] == 3 and headings[1]['end_line'] == 4


def test_unclosed_fence_keeps_following_heading_out_of_results():
    _, headings = markdown_headings('# Visible\n````\n# Hidden\n')

    assert [heading['name'] for heading in headings] == ['Visible [1]']


def test_unicode_line_separator_is_not_a_markdown_line_end():
    _, headings = markdown_headings('intro\u2028# Not a heading\n')

    assert headings == []


def test_form_feed_does_not_shift_markdown_heading_lines():
    raw, headings = markdown_headings('# A\n\x0c\n## B\n')

    section = next(heading for heading in headings if heading['name'] == 'A / B [1]')
    assert (section['start_line'], section['end_line']) == (3, 3)
    assert raw[section['start_byte']:section['end_byte']] == b'## B\n'
