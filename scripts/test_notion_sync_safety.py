"""Command-level regressions. The in-memory API models ordering and pagination.

Run: python3 -m unittest discover -s scripts -p 'test_notion_sync_safety.py' -v
"""
import copy
import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch

import notion_publish as np
import notion_sync as repair


def block(kind, text="", **fields):
    return {"object": "block", "type": kind, "has_children": False,
            kind: {"rich_text": [{"type": "text", "plain_text": text,
                                   "text": {"content": text}, "annotations": {},
                                   "href": None}], **fields}}


class Page:
    def __init__(self, blocks):
        self.children = {"page": []}
        self.blocks = {}
        self.writes = []
        self.comments = {}
        self.reject_chunk = None
        self.appends = 0
        self.corrupt_append = False
        self.fail_delete = None
        self.edit_old_on_append = None
        self.append_returns_siblings = False
        self.posted = []
        self.add("page", blocks)

    def add(self, parent, blocks, after=None):
        made = []
        for src in blocks:
            b = copy.deepcopy(src)
            b["id"] = str(len(self.blocks) + 1)
            body = b[b["type"]]
            nested = body.pop("children", [])
            b["has_children"] = bool(nested)
            for rt in [body.get("rich_text", [])] + body.get("cells", []):
                for r in rt:
                    r["plain_text"] = r["text"]["content"]
                    r["href"] = (r["text"].get("link") or {}).get("url")
            self.blocks[b["id"]] = b
            self.children[b["id"]] = []
            self.add(b["id"], nested)
            made.append(b)
        ids = self.children[parent]
        at = ids.index(after) + 1 if after else len(ids)
        ids[at:at] = [b["id"] for b in made]
        return made

    def api(self, path, method="GET", body=None, soft=False):
        route, _, query = path.partition("?")
        q = urllib.parse.parse_qs(query)
        if method != "GET":
            self.writes.append((method, route, body))
        if route == "/comments":
            if method == "POST":
                made = {"id": f"c{len(self.posted) + 1}", "discussion_id": body["discussion_id"],
                        "rich_text": body["rich_text"],
                        "created_by": {"id": "me"}, "created_time": "2026-09-19T00:00:00.000Z"}
                self.posted.append(made)
                return made
            value = self.comments.get(q["block_id"][0], [])
            return value if isinstance(value, dict) else {"results": value}
        bid = route.split("/")[2]
        if route.endswith("/children"):
            if method == "GET":
                start = int(q.get("start_cursor", [0])[0])
                ids = self.children[bid]
                return {"results": [copy.deepcopy(self.blocks[i]) for i in ids[start:start + 100]],
                        "has_more": start + 100 < len(ids), "next_cursor": str(start + 100)}
            self.appends += 1
            if self.appends == self.reject_chunk:
                return {"_error": 400, "_detail": "injected rejection"}
            if len(body["children"]) > 100:
                raise AssertionError("append exceeds API limit")
            at_start = body.get("position", {}).get("type") == "start"
            previous = list(self.children[bid])
            made = self.add(bid, body["children"], body.get("after"))
            if at_start:
                self.children[bid] = [b["id"] for b in made] + previous
            target = self.edit_old_on_append or (made[0]["id"] if self.corrupt_append else None)
            if target:
                b = self.blocks[target]
                b[b["type"]]["rich_text"][0]["plain_text"] = "concurrent or corrupt content"
            if self.append_returns_siblings:
                return {"results": [self.blocks[i] for i in self.children[bid][:100]]}
            return {"results": made}
        if method == "GET":
            return copy.deepcopy(self.blocks[bid])
        if method == "DELETE":
            if bid == self.fail_delete:
                return {"_error": 503, "_detail": "injected delete failure"}
            for ids in self.children.values():
                if bid in ids:
                    ids.remove(bid)
            return {}
        self.blocks[bid].update(copy.deepcopy(body))
        for kind, value in self.blocks[bid].items():
            if not isinstance(value, dict):
                continue
            for r in value.get("rich_text", []):
                r["plain_text"] = r["text"]["content"]
        return copy.deepcopy(self.blocks[bid])

    def texts(self, parent="page"):
        return [np.text_of(self.blocks[i]) for i in self.children[parent]]


class CommandCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        np.UNRESOLVED.clear()
        np.IMPORTED = np.PREAMBLE = np.ALLOW_FLATTEN = np.ALLOW_LINK_LOSS = False
        np.ALLOW_COMMENT_EDIT = np.WITH_COMMENTS = False

    def doc(self, body):
        path = os.path.join(self.tmp.name, "doc.md")
        with open(path, "w") as f:
            f.write("---\ntitle: Title\ntype: rfc\nimported: false\n---\n# Title\n" + body)
        return path

    def run_command(self, page, path, *args):
        return self.run_capturing(page, path, *args)[0]

    def run_capturing(self, page, path, *args):
        """The exit code and everything the command printed, for the reports."""
        out = io.StringIO()
        with patch.object(np, "api", page.api), patch.object(np, "page_id", return_value="page"), \
                patch.object(np.sys, "argv", ["notion_publish.py", args[0], path, *args[1:]]), \
                contextlib.redirect_stdout(out):
            try:
                result = np.main() or 0
            except SystemExit as e:
                result = e.code or 0
        return result, out.getvalue()

    def routes(self, page):
        return [(method, route) for method, route, _ in page.writes]


def comment(cid, discussion, who, text, when="2026-09-17T07:00:00.000Z"):
    return {"id": cid, "discussion_id": discussion, "created_time": when,
            "created_by": {"id": who}, "display_name": {"resolved_name": who},
            "rich_text": [{"plain_text": text}]}


class Comments(CommandCase):
    """The review loop: read the threads, answer one, publish under the rest."""

    def setUp(self):
        super().setUp()
        token = os.path.join(self.tmp.name, "token.json")
        with open(token, "w") as f:
            json.dump({"access_token": "x", "owner": {"user": {"id": "me"}}}, f)
        patcher = patch.object(np, "TOKEN", token)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_thread_on_a_table_is_reported_once_against_the_table(self):
        """Notion answers a table's thread for the table and again for every row. Listed
        per row, a five-row table turns one reviewer question into five action items."""
        row = block("table_row", "cell")
        table = block("table", "", children=[row])
        page = Page([block("heading_2", "Scope"), table])
        rows = page.children[page.children["page"][1]]
        page.comments = {page.children["page"][1]: [comment("c1", "d1", "Arif", "overridable?")],
                         rows[0]: [comment("c1", "d1", "Arif", "overridable?")]}
        code, out = self.run_capturing(page, self.doc("intro\n\n## Scope"), "comments")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("overridable?"), 1)
        self.assertIn("on table", out)
        self.assertIn("1 open thread(s), 1 waiting on you.", out)

    def test_a_thread_i_answered_last_is_not_waiting_on_me(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "body")])
        pid = page.children["page"][1]
        page.comments = {pid: [comment("c1", "d1", "Arif", "question?"),
                               comment("c2", "d1", "me", "answered", "2026-09-17T08:00:00.000Z")]}
        code, out = self.run_capturing(page, self.doc("intro\n\n## Scope\nbody"), "comments")
        self.assertNotIn("[waiting on you]", out)
        self.assertIn("0 waiting on you.", out)

    def test_a_thread_whose_anchor_text_was_deleted_says_so(self):
        """The API puts original_content_deleted on the comment, not on the block."""
        page = Page([block("heading_2", "Scope"), block("paragraph", "body")])
        pid = page.children["page"][1]
        gone = comment("c1", "d1", "Arif", "question?")
        gone["original_content_deleted"] = True
        page.comments = {pid: [gone]}
        code, out = self.run_capturing(page, self.doc("## Scope\nbody"), "comments")
        self.assertIn("anchored to has been deleted", out)

    def test_verify_lists_threads_waiting_on_me_without_failing(self):
        """A page can match the repository and still have review work outstanding."""
        page = Page([block("heading_2", "Scope"), block("paragraph", "body")])
        pid = page.children["page"][1]
        page.comments = {pid: [comment("c1", "d1", "Arif", "overridable?")]}
        code, out = self.run_capturing(page, self.doc("## Scope\nbody"), "verify", "--with-comments")
        self.assertEqual(code, 0)
        self.assertIn("open threads: 1, 1 waiting on you", out)
        self.assertIn("overridable?", out)

    def test_verify_reads_no_comments_without_the_flag(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "body")])
        page.comments = {"page": {"_error": 403, "_detail": "no comment access"}}
        code, out = self.run_capturing(page, self.doc("## Scope\nbody"), "verify")
        self.assertEqual(code, 0)
        self.assertIn("add --with-comments", out)

    def test_reply_refuses_a_discussion_that_is_not_on_this_page(self):
        """A discussion id from another document is a valid id. Posting to it puts words
        under someone else's work."""
        page = Page([block("heading_2", "Scope")])
        page.comments = {page.children["page"][0]: [comment("c1", "d1", "Arif", "question?")]}
        code, out = self.run_capturing(page, self.doc("intro\n\n## Scope"),
                                       "reply", "d-elsewhere", "answer")
        self.assertNotEqual(code, 0)
        self.assertEqual(page.posted, [])

    def test_reply_previews_the_thread_without_posting(self):
        page = Page([block("heading_2", "Scope")])
        page.comments = {page.children["page"][0]: [comment("c1", "d1", "Arif", "question?")]}
        code, out = self.run_capturing(page, self.doc("intro\n\n## Scope"),
                                       "reply", "d1", "hardcoded in v1")
        self.assertEqual(code, 0)
        self.assertIn("question?", out)
        self.assertIn("hardcoded in v1", out)
        self.assertEqual(page.posted, [])

    def test_reply_posts_into_the_named_discussion(self):
        page = Page([block("heading_2", "Scope")])
        page.comments = {page.children["page"][0]: [comment("c1", "d1", "Arif", "question?")]}
        code, out = self.run_capturing(page, self.doc("intro\n\n## Scope"),
                                       "reply", "d1", "hardcoded in v1", "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(len(page.posted), 1)
        self.assertEqual(page.posted[0]["discussion_id"], "d1")
        self.assertEqual(page.posted[0]["rich_text"][0]["text"]["content"], "hardcoded in v1")

    def test_a_commented_block_refuses_in_place_without_the_flag(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "old")])
        pid = page.children["page"][1]
        page.comments = {pid: [comment("c1", "d1", "Arif", "question?")]}
        code, out = self.run_capturing(page, self.doc("intro\n\n## Scope\nnew"), "apply", "## Scope")
        self.assertNotEqual(code, 0)
        self.assertIn("--allow-comment-edit", out + str(code))
        self.assertEqual(page.texts(), ["Scope", "old"])

    def test_the_flag_rewrites_the_commented_block_and_names_its_thread(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "old")])
        pid = page.children["page"][1]
        page.comments = {pid: [comment("c1", "d1", "Arif", "question?")]}
        code, out = self.run_capturing(page, self.doc("intro\n\n## Scope\nnew"),
                                       "apply", "## Scope", "--allow-comment-edit")
        self.assertEqual(code, 0)
        self.assertEqual(page.texts(), ["Scope", "new"])
        self.assertEqual(page.children["page"][1], pid, "the block kept its ID, so the thread survived")
        self.assertIn("THREAD UNDER REWRITE", out)
        self.assertIn("d1", out)

    def test_the_flag_does_not_permit_deleting_a_commented_block(self):
        """A deleted block takes its thread with it. No flag permits that; only a block
        that survives the write can keep a thread."""
        page = Page([block("heading_2", "Scope"), block("paragraph", "old")])
        pid = page.children["page"][1]
        page.comments = {pid: [comment("c1", "d1", "Arif", "question?")]}
        code, out = self.run_capturing(page, self.doc("intro\n\n## Scope\n- new"),
                                       "apply", "## Scope", "--allow-comment-edit")
        self.assertNotEqual(code, 0)
        self.assertEqual(page.texts(), ["Scope", "old"])
        self.assertEqual(page.writes, [])

    def test_a_split_paragraph_keeps_its_thread_on_the_first_half(self):
        """The case that used to need the reviewer to resolve first: one commented
        paragraph becomes two. The first keeps the block and its thread; the second is new."""
        page = Page([block("heading_2", "Scope"), block("paragraph", "old")])
        pid = page.children["page"][1]
        page.comments = {pid: [comment("c1", "d1", "Arif", "question?")]}
        code, out = self.run_capturing(page, self.doc("intro\n\n## Scope\nnew\n\nsecond line"),
                                       "apply", "## Scope", "--allow-comment-edit")
        self.assertEqual(code, 0)
        self.assertEqual(page.texts(), ["Scope", "new", "second line"])
        self.assertEqual(page.children["page"][1], pid)
        self.assertIn("THREAD UNDER REWRITE", out)

    def test_an_unreadable_thread_still_refuses_with_the_flag(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "old")])
        pid = page.children["page"][1]
        page.comments = {pid: {"_error": 403, "_detail": "no comment access"}}
        code, out = self.run_capturing(page, self.doc("intro\n\n## Scope\nnew"),
                                       "apply", "## Scope", "--allow-comment-edit")
        self.assertNotEqual(code, 0)
        self.assertEqual(page.texts(), ["Scope", "old"])


class SyncSafety(CommandCase):
    def test_verify_detects_stale_preamble(self):
        page = Page([block("paragraph", "old intro"), block("heading_2", "Scope"), block("paragraph", "same")])
        self.assertNotEqual(self.run_command(page, self.doc("new intro\n\n## Scope\nsame"), "verify"), 0)

    def test_verify_detects_missing_preamble(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "same")])
        self.assertNotEqual(self.run_command(page, self.doc("intro\n\n## Scope\nsame"), "verify"), 0)

    def test_verify_checks_tsd_intro_before_subsections(self):
        page = Page([block("heading_1", "Introduction"), block("paragraph", "stale"),
                     block("heading_2", "Context"), block("paragraph", "same")])
        self.assertNotEqual(self.run_command(page, self.doc("# Introduction\nnew\n\n## Context\nsame"), "verify"), 0)

    def test_verify_accepts_complete_tsd(self):
        page = Page([block("heading_1", "Introduction"), block("paragraph", "same")])
        self.assertEqual(self.run_command(page, self.doc("# Introduction\nsame"), "verify"), 0)

    def test_verify_detects_heading_only_drift(self):
        page = Page([block("heading_2", "1. OLD"), block("paragraph", "same")])
        self.assertNotEqual(self.run_command(page, self.doc("## 1. New\nsame"), "verify"), 0)

    def test_heading_only_apply_keeps_body_ids(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "same")])
        self.assertEqual(self.run_command(page, self.doc("## SCOPE\nsame"), "apply", "## SCOPE"), 0)
        self.assertEqual(page.texts(), ["SCOPE", "same"])
        self.assertEqual(page.children["page"], ["1", "2"])

    def test_toggle_reads_all_children(self):
        page = Page([block("heading_2", "Scope", is_toggleable=True,
                           children=[block("paragraph", str(i)) for i in range(101)])])
        with patch.object(np, "api", page.api):
            kids = np.top_children("page")
            self.assertEqual(len(np.section_blocks(kids, 0)), 101)

    def test_preamble_refuses_comment_read_failure(self):
        page = Page([block("paragraph", "old"), block("heading_2", "Scope")])
        page.comments["1"] = {"_error": 403, "_detail": "no comment access"}
        self.assertNotEqual(self.run_command(page, self.doc("new\n\n## Scope"), "apply", "@preamble"), 0)
        self.assertEqual(page.writes, [])

    def test_section_refuses_comment_read_failure(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "old")])
        page.comments["2"] = {"_error": 403, "_detail": "no comment access"}
        self.assertNotEqual(self.run_command(page, self.doc("## Scope\nnew"), "apply", "## Scope"), 0)
        self.assertEqual(page.writes, [])

    def test_drop_preserves_nested_page(self):
        page = Page([block("heading_2", "Old"), block("child_page", title="Child"), block("heading_2", "Scope")])
        self.run_command(page, self.doc("## Scope"), "drop", "Old", "--yes")
        self.assertIn("2", page.children["page"])

    def test_manual_insert_refuses(self):
        page = Page([block("paragraph", "intro"), block("heading_2", "Scope")])
        self.assertNotEqual(self.run_command(page, self.doc("## Approval <!-- manual -->\nprivate\n## Scope"),
                                             "insert", "## Approval", "Scope", "--yes"), 0)
        self.assertEqual(page.writes, [])

    def test_preamble_chunks_and_rolls_back_rejection(self):
        page = Page([block("paragraph", "old"), block("heading_2", "Scope")])
        page.reject_chunk = 2
        result = self.run_command(page, self.doc("\n\n".join(str(i) for i in range(102)) + "\n\n## Scope"),
                                  "apply", "@preamble")
        self.assertNotEqual(result, 0)
        self.assertEqual(page.texts(), ["old", "Scope"])

    def test_nested_content_cannot_be_silently_deleted(self):
        page = Page([block("heading_2", "Scope"), block("bulleted_list_item", "old",
                           children=[block("paragraph", "nested evidence")])])
        self.assertNotEqual(self.run_command(page, self.doc("## Scope\n- new"), "apply", "## Scope"), 0)
        self.assertEqual(page.writes, [])

    def test_ambiguous_heading_refuses(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "first"),
                     block("heading_2", "Scope"), block("paragraph", "second")])
        self.assertNotEqual(self.run_command(page, self.doc("## Scope\nnew"), "apply", "## Scope"), 0)
        self.assertEqual(page.writes, [])

    def test_long_prose_respects_rich_text_limit(self):
        pieces = np.rich("x" * 4001)
        self.assertTrue(all(len(p["text"]["content"]) <= 2000 for p in pieces))
        self.assertEqual("".join(p["text"]["content"] for p in pieces), "x" * 4001)

    def test_h1_is_a_heading_block(self):
        self.assertEqual(np.convert(["# Introduction"])[0]["type"], "heading_1")

    def test_timeout_does_not_retry_append(self):
        with patch.object(np.Path, "read_text", return_value='{"access_token":"test"}'), \
                patch.object(np.urllib.request, "urlopen", side_effect=TimeoutError("lost reply")) as request, \
                patch.object(np.time, "sleep"):
            with self.assertRaises(SystemExit):
                np.api("/blocks/page/children", "PATCH", {"children": []})
        self.assertEqual(request.call_count, 1)

    def test_repeated_apply_preserves_ids(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "old")])
        path = self.doc("## Scope\nnew")
        self.assertEqual(self.run_command(page, path, "apply", "## Scope"), 0)
        self.assertEqual(page.texts(), ["Scope", "new"])
        ids = list(page.children["page"])
        page.writes.clear()
        self.assertEqual(self.run_command(page, path, "apply", "## Scope"), 0)
        self.assertEqual(page.children["page"], ids)
        self.assertEqual(page.writes, [])

    def test_verify_detects_wrong_section_order(self):
        page = Page([block("heading_2", "Second"), block("paragraph", "two"),
                     block("heading_2", "First"), block("paragraph", "one")])
        self.assertNotEqual(self.run_command(page, self.doc("## First\none\n\n## Second\ntwo"), "verify"), 0)

    def test_manual_descendant_cannot_be_published(self):
        page = Page([block("heading_2", "Approval"), block("heading_3", "Evidence"), block("paragraph", "old")])
        path = self.doc("## Approval <!-- manual -->\n### Evidence\nnew")
        self.assertNotEqual(self.run_command(page, path, "apply", "### Evidence"), 0)
        self.assertEqual(page.writes, [])

    def test_manual_image_is_reported_without_failing_managed_body(self):
        page = Page([block("heading_2", "Approval", is_toggleable=True,
                           children=[block("image")]), block("heading_2", "Scope"), block("paragraph", "same")])
        self.assertEqual(self.run_command(page, self.doc("## Approval <!-- manual --> <!-- toggle -->\nmanual\n\n## Scope\nsame"), "verify"), 0)

    def test_nested_page_cannot_be_flattened(self):
        page = Page([block("heading_2", "Scope"), block("bulleted_list_item", "old",
                           children=[block("child_page", title="Evidence")])])
        self.assertNotEqual(self.run_command(page, self.doc("## Scope\nnew"), "apply", "## Scope", "--allow-flatten"), 0)
        self.assertEqual(page.writes, [])

    def test_table_rows_are_paginated(self):
        page = Page([block("table", children=[block("table_row", cells=[]) for _ in range(101)])])
        with patch.object(np, "api", page.api):
            self.assertEqual(len(np.rendered_page_lines(np.top_children("page"))), 101)

    def test_preamble_refuses_losing_link(self):
        old = block("paragraph", "linked")
        old["paragraph"]["rich_text"][0]["text"]["link"] = {"url": "https://example.org"}
        page = Page([old, block("heading_2", "Scope")])
        self.assertNotEqual(self.run_command(page, self.doc("new\n\n## Scope"), "apply", "@preamble"), 0)
        self.assertEqual(page.writes, [])

    def test_a_target_outside_the_notion_workflow_flattens_without_blocking(self):
        """`knowledge/` and `performance/` carry no `published_url` field at all, because
        CLAUDE.md keeps them out of the publishing workflow. A reference to one can never
        acquire a page, so treating it as a missing destination would fail verification
        forever. It flattens to plain text instead."""
        finding = os.path.join(self.tmp.name, "finding.md")
        with open(finding, "w") as f:
            f.write("---\ntitle: Finding\ntype: finding\nsource_url:\n---\n# Finding\n")
        np.BASE_DIR = self.tmp.name
        self.assertIsNone(np.published_url_of("finding.md"))
        self.assertEqual(np.UNRESOLVED, [])

    def test_an_unpublished_document_is_still_a_missing_destination(self):
        """A document that carries the field and leaves it empty is waiting for its page,
        which is the blocker the guard exists for."""
        target = os.path.join(self.tmp.name, "tsd.md")
        with open(target, "w") as f:
            f.write("---\ntitle: TSD\ntype: tsd\npublished_url:\nimported: false\n---\n# TSD\n")
        np.BASE_DIR = self.tmp.name
        self.assertIsNone(np.published_url_of("tsd.md"))
        self.assertEqual(np.UNRESOLVED, ["tsd.md"])

    def test_only_the_first_position_append_uses_the_newer_api_version(self):
        self.assertEqual(np.api_version("/blocks/1/children",
                                        {"children": [], "position": {"type": "start"}}),
                         "2026-03-11")
        self.assertEqual(np.api_version("/blocks/1/children", {"children": []}), np.VERSION)
        self.assertEqual(np.api_version("/blocks/1", {"paragraph": {}}), np.VERSION)

    def test_coverage_reports_without_writing(self):
        page = Page([block("paragraph", "intro"), block("heading_2", "Scope"), block("paragraph", "same")])
        self.assertEqual(self.run_command(page, self.doc("intro\n\n## Scope\nsame"), "coverage") or 0, 0)
        self.assertEqual(page.writes, [])

    def test_allow_link_loss_permits_the_reported_write(self):
        """The flag exists for a link that has no destination to keep. It permits that
        one write; it never makes the document verify."""
        old = block("paragraph", "linked")
        old["paragraph"]["rich_text"][0]["text"]["link"] = {"url": "https://example.org"}
        page = Page([old, block("heading_2", "Scope")])
        path = self.doc("new\n\n## Scope")
        self.assertEqual(self.run_command(page, path, "apply", "@preamble", "--allow-link-loss"), 0)
        self.assertEqual(page.texts(), ["new", "Scope"])

    def test_emoji_quote_is_a_callout_and_a_plain_quote_is_not(self):
        """The emoji decides the block type, so a quotation keeps its wording in a quote
        block and an authored aside becomes a callout with that emoji as its icon."""
        blocks = np.convert(["> \U0001f4a1 Authored aside.", "", "> Someone else's words."])
        self.assertEqual([b["type"] for b in blocks], ["callout", "quote"])
        self.assertEqual(blocks[0]["callout"]["icon"], {"type": "emoji", "emoji": "\U0001f4a1"})
        self.assertEqual(blocks[0]["callout"]["rich_text"][0]["text"]["content"], "Authored aside.")

    def test_checkboxes_dividers_and_strikethrough_convert(self):
        blocks = np.convert(["- [ ] open", "- [x] done", "", "---", "", "a ~~cut~~ word"])
        self.assertEqual([b["type"] for b in blocks],
                         ["to_do", "to_do", "divider", "paragraph"])
        self.assertEqual([b["to_do"]["checked"] for b in blocks[:2]], [False, True])
        struck = [r for r in blocks[3]["paragraph"]["rich_text"]
                  if r.get("annotations", {}).get("strikethrough")]
        self.assertEqual([r["text"]["content"] for r in struck], ["cut"])

    def test_a_callout_and_a_to_do_survive_a_page_round_trip(self):
        """Conversion is only half of it: a block the renderer cannot describe compares
        as different on every run and rewrites a section that never changed."""
        body = "## Scope\n> \U0001f4a1 Aside.\n\n- [x] done"
        page = Page([block("heading_2", "Scope")] + np.convert(body.split("\n")[1:]))
        self.assertEqual(self.run_command(page, self.doc(body), "verify"), 0)
        self.assertEqual(page.writes, [])

    def test_nested_lists_preserve_parent_relationship(self):
        result = np.convert(["- Parent", "  - Child", "- Sibling"])
        self.assertEqual(len(result), 2)
        children = result[0]["bulleted_list_item"].get("children", [])
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["bulleted_list_item"]["rich_text"][0]["text"]["content"], "Child")

    def test_four_backtick_example_keeps_inner_fence_and_heading(self):
        lines = ["````markdown", "# Example", "```go", "sample", "```", "````"]
        result = np.convert(lines)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["code"]["language"], "markdown")
        self.assertEqual(result[0]["code"]["rich_text"][0]["text"]["content"],
                         "# Example\n```go\nsample\n```")
        self.assertTrue(all(not line for line in np.mask_fences(lines)))

    def test_nested_fence_does_not_expose_local_example(self):
        lines = ["````markdown", "```text", np.LOCAL_START, "sample", np.LOCAL_END, "```", "````"]
        self.assertEqual(np.strip_local(lines), lines)

    def test_images_refuse_instead_of_becoming_broken_text(self):
        with self.assertRaises(SystemExit):
            np.convert(["See ![proof](./proof.png)"])

    def test_unknown_language_refuses_instead_of_losing_highlighting(self):
        with self.assertRaises(SystemExit):
            np.convert(["```not-a-language", "sample", "```"])

    def test_unclosed_fence_refuses(self):
        with self.assertRaises(SystemExit):
            np.convert(["```go", "sample"])

    def test_comment_guard_cannot_be_bypassed_by_paragraph_continuation(self):
        with self.assertRaises(SystemExit):
            np.convert(["public", "%%private%%"])

    def test_rate_limit_retries_after_header(self):
        error = urllib.error.HTTPError("https://api.notion.com", 429, "rate limited", {"Retry-After": "1"}, io.BytesIO(b"{}"))
        with patch.object(np.Path, "read_text", return_value='{"access_token":"test"}'), \
                patch.object(np.urllib.request, "urlopen", side_effect=[error, io.BytesIO(b'{"results":[]}')]), \
                patch.object(np.time, "sleep") as sleep, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(np.api("/blocks/page/children", "PATCH", {"children": []}), {"results": []})
            sleep.assert_called_once_with(1)

    def test_failed_readback_preserves_original(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "old one"), block("paragraph", "old two")])
        page.corrupt_append = True
        self.assertNotEqual(self.run_command(page, self.doc("## Scope\n- new"), "apply", "## Scope"), 0)
        self.assertIn("2", page.children["page"])
        self.assertEqual(np.text_of(page.blocks["2"]), "old one")

    def test_concurrent_edit_preserved_before_delete(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "old one"), block("paragraph", "old two")])
        page.edit_old_on_append = "2"
        self.assertNotEqual(self.run_command(page, self.doc("## Scope\n- new"), "apply", "## Scope"), 0)
        self.assertIn("2", page.children["page"])

    def test_partial_delete_reports_failure_and_keeps_new_content(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "old one"), block("paragraph", "old two")])
        page.fail_delete = "3"
        self.assertNotEqual(self.run_command(page, self.doc("## Scope\nnew"), "apply", "## Scope"), 0)
        self.assertEqual(page.texts(), ["Scope", "new", "old two"])

    def test_segment_boundaries_do_not_trigger_rewrite(self):
        b = block("paragraph", "same")
        b["paragraph"]["rich_text"][0]["text"]["content"] = "sa"
        b["paragraph"]["rich_text"].append({"type": "text", "text": {"content": "me"}})
        page = Page([block("heading_2", "Scope"), b])
        self.assertEqual(self.run_command(page, self.doc("## Scope\nsame"), "apply", "## Scope"), 0)
        self.assertEqual(page.writes, [])

    def test_repeated_subheadings_resolve_within_parent(self):
        page = Page([block("heading_2", "First"), block("heading_3", "Details"), block("paragraph", "one"),
                     block("heading_2", "Second"), block("heading_3", "Details"), block("paragraph", "two")])
        path = self.doc("## First\n### Details\none\n## Second\n### Details\ntwo")
        self.assertEqual(self.run_command(page, path, "verify"), 0)
        path = self.doc("## First\n### Details\none\n## Second\n### Details\nupdated")
        self.assertEqual(self.run_command(page, path, "apply", "## Second > ### Details"), 0)
        self.assertEqual(page.texts(), ["First", "Details", "one", "Second", "Details", "updated"])

    def test_whitespace_repair_ignores_local_code(self):
        path = self.doc("<!-- local:start -->\n```go\nprivate code\n```\n<!-- local:end -->\n## Scope\n```go\npublic code\n```")
        self.assertEqual([c.rstrip("\n") for c in repair.repo_fences(path)], ["public code"])

    def test_compact_table_preserves_empty_cells_and_escaped_pipe(self):
        table = np.convert(["|A|B|C|", "|---|---|---|", r"||a\|b|last|"])[0]["table"]
        self.assertEqual(table["table_width"], 3)
        self.assertEqual(["".join(r["text"]["content"] for r in c)
                          for c in table["children"][1]["table_row"]["cells"]], ["", "a|b", "last"])

    def test_plan_shows_nested_list_change(self):
        page = Page([block("heading_2", "Scope"), block("bulleted_list_item", "Parent",
                           children=[block("bulleted_list_item", "old child")])])
        path = self.doc("## Scope\n- Parent\n  - new child")
        output = io.StringIO()
        with patch.object(np, "api", page.api), patch.object(np, "page_id", return_value="page"), \
                patch.object(np.sys, "argv", ["notion_publish.py", "plan", path, "## Scope"]), \
                contextlib.redirect_stdout(output):
            np.main()
        self.assertIn("old child", output.getvalue())
        self.assertIn("new child", output.getvalue())
        self.assertEqual(page.writes, [])

    def test_manual_link_failure_is_scoped_to_its_region(self):
        page = Page([block("paragraph", "missing"), block("heading_2", "Scope"), block("paragraph", "same")])
        path = self.doc("[missing](missing.md)\n\n## Scope\nsame")
        output = io.StringIO()
        with patch.object(np, "api", page.api), patch.object(np, "page_id", return_value="page"), \
                contextlib.redirect_stdout(output):
            np.cmd_manual(path)
        self.assertIn("IN SYNC: ## Scope", output.getvalue())

    def test_new_preamble_is_inserted_before_first_heading(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "same")])
        self.assertEqual(self.run_command(page, self.doc("intro\n\n## Scope\nsame"), "apply", "@preamble"), 0)
        self.assertEqual(page.texts(), ["intro", "Scope", "same"])
        self.assertEqual(page.children["page"][1:], ["1", "2"])

    def test_append_response_may_include_existing_siblings(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "old")])
        page.append_returns_siblings = True
        self.assertEqual(self.run_command(page, self.doc("## Scope\nnew"), "apply", "## Scope"), 0)
        self.assertEqual(page.texts(), ["Scope", "new"])

    def test_append_beyond_first_response_page_is_reconciled(self):
        page = Page([block("paragraph", str(i)) for i in range(101)] +
                    [block("heading_2", "Scope"), block("paragraph", "old")])
        page.append_returns_siblings = True
        self.assertEqual(self.run_command(page, self.doc("## Scope\nnew"), "apply", "## Scope"), 0)
        self.assertEqual(page.texts()[-2:], ["Scope", "new"])


class InPlaceUpdate(CommandCase):
    """A section whose blocks line up 1:1 is edited block by block, so identical blocks
    keep their IDs, their comment threads, and their content untouched."""

    def test_only_the_differing_block_is_written(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "same"), block("paragraph", "old")])
        self.assertEqual(self.run_command(page, self.doc("## Scope\nsame\n\nnew"), "apply", "## Scope"), 0)
        self.assertEqual(self.routes(page), [("PATCH", "/blocks/3")])
        self.assertEqual(page.texts(), ["Scope", "same", "new"])
        self.assertEqual(page.children["page"], ["1", "2", "3"])

    def test_comment_on_an_untouched_block_does_not_block_the_section(self):
        """The case from the lint-pipeline TSD: one code block differs, and the section's
        opening paragraph carries an unresolved reviewer comment."""
        page = Page([block("heading_2", "Scope"), block("paragraph", "same"),
                     block("code", "graph TD", language="mermaid")])
        page.comments["2"] = [{"id": "c1"}]
        self.assertEqual(self.run_command(
            page, self.doc("## Scope\nsame\n\n```mermaid\ngraph LR\n```"), "apply", "## Scope"), 0)
        self.assertEqual(self.routes(page), [("PATCH", "/blocks/3")])
        self.assertEqual(page.children["page"], ["1", "2", "3"])

    def test_comment_on_the_differing_block_still_refuses(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "same"), block("paragraph", "old")])
        page.comments["3"] = [{"id": "c1"}]
        self.assertNotEqual(self.run_command(page, self.doc("## Scope\nsame\n\nnew"), "apply", "## Scope"), 0)
        self.assertEqual(page.writes, [])

    def test_a_nested_child_is_updated_without_touching_its_parent(self):
        page = Page([block("heading_2", "Scope"),
                     block("bulleted_list_item", "Parent",
                           children=[block("bulleted_list_item", "old child")])])
        self.assertEqual(self.run_command(page, self.doc("## Scope\n- Parent\n  - new child"),
                                          "apply", "## Scope"), 0)
        self.assertEqual(self.routes(page), [("PATCH", "/blocks/3")])
        self.assertEqual(np.text_of(page.blocks["3"]), "new child")

    def test_an_added_block_is_created_and_the_rest_keep_their_ids(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "same"), block("paragraph", "old")])
        self.assertEqual(self.run_command(page, self.doc("## Scope\nsame\n\nnew\n\nextra"),
                                          "apply", "## Scope"), 0)
        self.assertEqual([r for r in self.routes(page) if r[0] == "DELETE"], [])
        self.assertEqual(page.texts(), ["Scope", "same", "new", "extra"])
        self.assertEqual(page.children["page"][:3], ["1", "2", "3"])

    def test_a_block_inserted_mid_section_lands_in_place(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "first"), block("paragraph", "last")])
        self.assertEqual(self.run_command(page, self.doc("## Scope\nfirst\n\n- middle\n\nlast"),
                                          "apply", "## Scope"), 0)
        self.assertEqual(page.texts(), ["Scope", "first", "middle", "last"])
        self.assertEqual([r[0] for r in self.routes(page)], ["PATCH"])

    def test_a_removed_block_is_deleted_alone(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "keep"),
                     block("paragraph", "gone"), block("paragraph", "also keep")])
        self.assertEqual(self.run_command(page, self.doc("## Scope\nkeep\n\nalso keep"),
                                          "apply", "## Scope"), 0)
        self.assertEqual(self.routes(page), [("DELETE", "/blocks/3")])
        self.assertEqual(page.texts(), ["Scope", "keep", "also keep"])

    def test_a_block_added_before_the_first_one_lands_after_the_heading(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "body")])
        self.assertEqual(self.run_command(page, self.doc("## Scope\n- lead\n\nbody"),
                                          "apply", "## Scope"), 0)
        self.assertEqual(page.texts(), ["Scope", "lead", "body"])
        self.assertEqual(page.children["page"][2], "2")

    def test_a_list_item_that_gains_a_child_keeps_its_id(self):
        page = Page([block("heading_2", "Scope"), block("bulleted_list_item", "Parent")])
        self.assertEqual(self.run_command(page, self.doc("## Scope\n- Parent\n  - child"),
                                          "apply", "## Scope"), 0)
        self.assertEqual(page.children["page"], ["1", "2"])
        self.assertEqual(page.texts("2"), ["child"])

    def test_a_table_row_is_added_without_rebuilding_the_table(self):
        def row(*cells):
            return {"object": "block", "type": "table_row", "has_children": False,
                    "table_row": {"cells": [[{"type": "text", "plain_text": c, "text": {"content": c},
                                              "annotations": {}, "href": None}] for c in cells]}}
        table = {"object": "block", "type": "table", "has_children": True,
                 "table": {"table_width": 2, "has_column_header": True, "has_row_header": False,
                           "children": [row("a", "b"), row("1", "2")]}}
        page = Page([block("heading_2", "Scope"), table])
        self.assertEqual(self.run_command(page, self.doc("## Scope\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"),
                                          "apply", "## Scope"), 0)
        self.assertEqual(page.children["page"], ["1", "2"])
        self.assertEqual(len(page.children["2"]), 3)
        self.assertEqual([r[0] for r in self.routes(page)], ["PATCH"])

    def test_a_table_that_gains_a_column_is_rebuilt(self):
        """Notion cannot change a table's width in place, so the table is recreated."""
        def row(*cells):
            return {"object": "block", "type": "table_row", "has_children": False,
                    "table_row": {"cells": [[{"type": "text", "plain_text": c, "text": {"content": c},
                                              "annotations": {}, "href": None}] for c in cells]}}
        table = {"object": "block", "type": "table", "has_children": True,
                 "table": {"table_width": 2, "has_column_header": True, "has_row_header": False,
                           "children": [row("a", "b")]}}
        page = Page([block("heading_2", "Scope"), table])
        self.assertEqual(self.run_command(page, self.doc("## Scope\n| a | b | c |\n|---|---|---|"),
                                          "apply", "## Scope"), 0)
        self.assertIn(("DELETE", "/blocks/2"), self.routes(page))

    def test_a_changed_block_type_falls_back_to_replacement(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "old")])
        self.assertEqual(self.run_command(page, self.doc("## Scope\n- new"), "apply", "## Scope"), 0)
        self.assertIn(("DELETE", "/blocks/2"), self.routes(page))
        self.assertEqual(page.texts(), ["Scope", "new"])

    def test_a_comment_on_an_untouched_block_survives_an_added_block(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "same"), block("paragraph", "old")])
        page.comments["2"] = [{"id": "c1"}]
        self.assertEqual(self.run_command(page, self.doc("## Scope\nsame\n\nnew\n\nextra"),
                                          "apply", "## Scope"), 0)
        self.assertEqual(page.children["page"][1], "2")

    def test_a_comment_on_a_deleted_block_refuses(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "same"), block("paragraph", "gone")])
        page.comments["3"] = [{"id": "c1"}]
        self.assertNotEqual(self.run_command(page, self.doc("## Scope\nsame"), "apply", "## Scope"), 0)
        self.assertEqual(page.writes, [])

    def test_a_comment_inside_a_deleted_block_refuses(self):
        page = Page([block("heading_2", "Scope"),
                     block("bulleted_list_item", "Parent", children=[block("bulleted_list_item", "child")])])
        page.comments["3"] = [{"id": "c1"}]
        self.assertNotEqual(self.run_command(page, self.doc("## Scope\nprose"), "apply", "## Scope"), 0)
        self.assertEqual(page.writes, [])

    def test_an_unreadable_comment_on_a_patched_block_refuses(self):
        page = Page([block("heading_2", "Scope"), block("paragraph", "old")])
        page.comments["2"] = {"_error": 403, "_detail": "no comment access"}
        self.assertNotEqual(self.run_command(page, self.doc("## Scope\nnew"), "apply", "## Scope"), 0)
        self.assertEqual(page.writes, [])

    def test_plan_does_not_block_on_an_untouched_unrepresentable_block(self):
        captioned = block("code", "sample", language="go")
        captioned["code"]["caption"] = [{"type": "text", "plain_text": "a reviewer note"}]
        page = Page([block("heading_2", "Scope"), captioned, block("paragraph", "old")])
        output = io.StringIO()
        with patch.object(np, "api", page.api), patch.object(np, "page_id", return_value="page"), \
                patch.object(np.sys, "argv",
                             ["notion_publish.py", "plan",
                              self.doc("## Scope\n```go\nsample\n```\n\nnew"), "## Scope"]), \
                contextlib.redirect_stdout(output):
            np.main()
        self.assertNotIn("BLOCKED", output.getvalue())
        self.assertEqual(page.writes, [])

    def test_an_untouched_unrepresentable_block_is_left_alone_on_apply(self):
        captioned = block("code", "sample", language="go")
        captioned["code"]["caption"] = [{"type": "text", "plain_text": "a reviewer note"}]
        page = Page([block("heading_2", "Scope"), captioned, block("paragraph", "old")])
        self.assertEqual(self.run_command(page, self.doc("## Scope\n```go\nsample\n```\n\nnew"),
                                          "apply", "## Scope"), 0)
        self.assertEqual(self.routes(page), [("PATCH", "/blocks/3")])
        self.assertEqual(page.blocks["2"]["code"]["caption"][0]["plain_text"], "a reviewer note")

    def test_an_unrepresentable_block_that_differs_still_refuses(self):
        coloured = block("paragraph", "old")
        coloured["paragraph"]["color"] = "red"
        page = Page([block("heading_2", "Scope"), coloured])
        self.assertNotEqual(self.run_command(page, self.doc("## Scope\nnew"), "apply", "## Scope"), 0)
        self.assertEqual(page.writes, [])

    def test_a_link_lost_from_a_patched_block_refuses(self):
        old = block("paragraph", "linked")
        old["paragraph"]["rich_text"][0]["text"]["link"] = {"url": "https://example.org"}
        page = Page([block("heading_2", "Scope"), old])
        self.assertNotEqual(self.run_command(page, self.doc("## Scope\nplain"), "apply", "## Scope"), 0)
        self.assertEqual(page.writes, [])


if __name__ == "__main__":
    unittest.main()
