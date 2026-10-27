"""Plain toggle blocks written with toggle-block markers; no network access.

    python3 -m unittest test_notion_toggle_block
"""
import copy
import tempfile
import unittest
from pathlib import Path

import notion_publish as publish

START = '<!-- toggle-block: Shared workflow file -->'
END = '<!-- toggle-block:end -->'


def as_live(blocks):
    """The shape the API returns: plain_text and href beside text.content."""
    live = copy.deepcopy(blocks)
    for b in publish.descendants(live):
        body = b[b['type']]
        for rt in [body.get('rich_text', [])] + body.get('cells', []):
            for r in rt:
                r['plain_text'] = r['text']['content']
                r['href'] = (r['text'].get('link') or {}).get('url')
    return live


class ToggleBlockTests(unittest.TestCase):
    def test_converts_title_and_children(self):
        blocks = publish.convert(['Intro.', '', START, '```yaml', 'a: 1', '```', END, '', 'After.'])
        self.assertEqual([b['type'] for b in blocks], ['paragraph', 'toggle', 'paragraph'])
        toggle = blocks[1]['toggle']
        self.assertEqual(toggle['rich_text'][0]['text']['content'], 'Shared workflow file')
        self.assertEqual([c['type'] for c in toggle['children']], ['code'])
        self.assertEqual(toggle['children'][0]['code']['rich_text'][0]['text']['content'], 'a: 1')

    def test_title_keeps_inline_code(self):
        blocks = publish.convert(['<!-- toggle-block: The `api-lint.yml` file -->', 'Body.', END])
        self.assertTrue(any(r.get('annotations', {}).get('code')
                            for r in blocks[0]['toggle']['rich_text']))

    def test_paragraph_does_not_swallow_marker(self):
        blocks = publish.convert(['Intro line.', START, 'Body.', END])
        self.assertEqual([b['type'] for b in blocks], ['paragraph', 'toggle'])

    def test_marker_inside_fence_is_code(self):
        blocks = publish.convert(['```markdown', START, END, '```'])
        self.assertEqual([b['type'] for b in blocks], ['code'])

    def test_refusals(self):
        cases = {
            'unclosed toggle': [START, 'Body.'],
            'end with no start': [END],
            'nested toggle': [START, START, 'x', END, END],
            'heading inside toggle': [START, '#### Heading', END],
        }
        for name, lines in cases.items():
            with self.subTest(name):
                with self.assertRaises(SystemExit):
                    publish.convert(lines)

    def test_page_copy_is_in_sync_and_lossless(self):
        new = publish.convert([START, '```yaml', 'a: 1', '```', END])
        live = as_live(new)
        self.assertTrue(publish.same_blocks(live, new))
        self.assertEqual(publish.loss_reasons(live), [])

    def test_changed_child_is_a_difference(self):
        new = publish.convert([START, '```yaml', 'a: 1', '```', END])
        live = as_live(publish.convert([START, '```yaml', 'a: 2', '```', END]))
        self.assertFalse(publish.same_blocks(live, new))

    def test_render_shows_title_and_indented_children(self):
        lines = publish.lines_of(publish.convert([START, 'Body.', END]), False)
        self.assertEqual(lines, ['▸ Shared workflow file', '  Body.'])

    def test_nested_toggle_on_page_is_still_a_loss(self):
        outer = publish.convert([START, 'x', END])
        outer[0]['toggle']['children'] = publish.convert([START, 'Body.', END])
        self.assertIn('nested toggle', publish.loss_reasons(as_live(outer)))

    def test_section_body_keeps_toggle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.md'
            path.write_text('---\ntype: tsd\n---\n# Title\n# Section\n#### Reusable workflow\nText.\n\n'
                            f'{START}\n```yaml\n# a comment, not a heading\na: 1\n```\n{END}\n'
                            '#### Next\nOther.\n')
            heading, body = publish.repo_section(str(path), '#### Reusable workflow')
            self.assertEqual(heading, '#### Reusable workflow')
            self.assertNotIn('Other.', body)
            self.assertEqual([b['type'] for b in publish.convert(body)], ['paragraph', 'toggle'])


if __name__ == '__main__':
    unittest.main()
