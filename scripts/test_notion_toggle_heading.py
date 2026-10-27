"""Toggle headings written with their section nested inside; no network access.

    python3 -m unittest test_notion_toggle_heading
"""
import copy
import tempfile
import unittest
from pathlib import Path

import notion_publish as publish


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


def title(block):
    return ''.join(r['text']['content'] for r in block[block['type']]['rich_text'])


class ToggleHeadingTests(unittest.TestCase):
    def test_section_nests_inside_heading(self):
        blocks = publish.convert(['## Scored <!-- toggle -->', '- one', '- two', '## Next', 'After.'])
        self.assertEqual([b['type'] for b in blocks], ['heading_2', 'heading_2', 'paragraph'])
        head = blocks[0]['heading_2']
        self.assertTrue(head['is_toggleable'])
        self.assertEqual(title(blocks[0]), 'Scored')
        self.assertEqual([c['type'] for c in head['children']], ['bulleted_list_item', 'bulleted_list_item'])
        self.assertNotIn('is_toggleable', blocks[1]['heading_2'])

    def test_deeper_headings_stay_inside(self):
        blocks = publish.convert(['## Toggle <!-- toggle -->', 'Intro.', '### Sub', 'Body.', '## Next'])
        self.assertEqual([b['type'] for b in blocks], ['heading_2', 'heading_2'])
        self.assertEqual([c['type'] for c in blocks[0]['heading_2']['children']],
                         ['paragraph', 'heading_3', 'paragraph'])

    def test_fenced_hash_does_not_end_the_section(self):
        blocks = publish.convert(['### Benchmark <!-- toggle -->', '```bash', '# a comment', '```', 'After.'])
        self.assertEqual([b['type'] for b in blocks], ['heading_3'])
        self.assertEqual([c['type'] for c in blocks[0]['heading_3']['children']], ['code', 'paragraph'])

    def test_page_copy_with_table_is_in_sync_and_lossless(self):
        new = publish.convert(['### Benchmark <!-- toggle -->', 'Intro.', '| a | b |', '|---|---|', '| 1 | 2 |'])
        live = as_live(new)
        self.assertTrue(publish.same_blocks(live, new))
        self.assertEqual(publish.loss_reasons(live), [])

    def test_live_toggle_heading_matches_marked_line(self):
        live = as_live(publish.convert(['## Scored <!-- toggle -->', 'Body.']))[0]
        self.assertTrue(publish.heading_matches(live, '## Scored <!-- toggle -->'))
        self.assertFalse(publish.heading_matches(live, '## Scored'))

    def test_nesting_beyond_two_levels_is_refused_before_writing(self):
        blocks = publish.convert(['## Outer <!-- toggle -->', '### Inner <!-- toggle -->',
                                  '| a | b |', '|---|---|', '| 1 | 2 |'])
        with self.assertRaises(SystemExit):
            publish.validate_payload(blocks)

    def test_two_levels_are_accepted(self):
        blocks = publish.convert(['### Benchmark <!-- toggle -->', '| a | b |', '|---|---|', '| 1 | 2 |'])
        publish.validate_payload(blocks)

    def test_whole_section_with_nested_toggles_converts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.md'
            path.write_text('---\ntype: tsd\n---\n# Title\n# Alternative <!-- toggle -->\nIntro.\n'
                            '## Scored <!-- toggle -->\n- weights\n'
                            '## Linter\n### Benchmark <!-- toggle -->\nNumbers.\n'
                            '# Proposed\nOther.\n')
            heading, body = publish.repo_section(str(path), '# Alternative')
            blocks = publish.convert(body)
            self.assertEqual([b['type'] for b in blocks], ['paragraph', 'heading_2', 'heading_2', 'heading_3'])
            self.assertTrue(blocks[1]['heading_2']['is_toggleable'])
            self.assertTrue(blocks[3]['heading_3']['is_toggleable'])
            self.assertNotIn('Other.', body)

class ToggleSectionStrayBlockTests(unittest.TestCase):
    """A toggle heading owns its whole section. Content left beside it on the page is
    orphaned by a write that goes inside the toggle, so the write refuses instead."""

    def page(self, *types):
        out = []
        for i, t in enumerate(types):
            if t == 'toggle_h2':
                b = publish.convert(['## Scored <!-- toggle -->'])[0]
            elif t == 'h2':
                b = publish.convert([f'## Other{i}'])[0]
            elif t == 'child':
                b = {'type': 'child_page', 'child_page': {'title': 'Nested'}}
            else:
                b = publish.convert([f'Stray {i}.'])[0]
            b['id'] = f'id-{i}'
            out.append(b)
        return out

    def test_toggle_followed_by_next_heading_is_clean(self):
        kids = self.page('toggle_h2', 'h2', 'para')
        publish.assert_toggle_section_clean(kids, 0)

    def test_toggle_at_end_of_page_is_clean(self):
        kids = self.page('toggle_h2')
        publish.assert_toggle_section_clean(kids, 0)

    def test_blocks_beside_the_toggle_refuse_the_write(self):
        kids = self.page('toggle_h2', 'para', 'para', 'h2')
        with self.assertRaises(SystemExit) as caught:
            publish.assert_toggle_section_clean(kids, 0)
        self.assertIn('both inside the toggle and beside it', str(caught.exception))

    def test_preserved_nested_document_does_not_refuse(self):
        kids = self.page('toggle_h2', 'child', 'h2')
        publish.assert_toggle_section_clean(kids, 0)


if __name__ == '__main__':
    unittest.main()
