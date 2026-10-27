"""Tests for the pure parts of notion_publish.py: local blocks, section depth, and the
preamble region. These are the functions that decide what leaves the repository, and a
wrong answer from any of them either publishes something private or deletes a page
section, so they are tested rather than exercised by hand.

    python3 scripts/test_notion_publish.py

Nothing here touches the network or needs a token.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notion_publish as np

FAILED = []
CHECKS = 0


def check(name, got, want):
    global CHECKS
    CHECKS += 1
    if got == want:
        return
    FAILED.append(f"{name}\n     got:  {got!r}\n     want: {want!r}")


def refuses(name, fn):
    global CHECKS
    CHECKS += 1
    try:
        fn()
    except SystemExit:
        return
    FAILED.append(f"{name}\n     expected a refusal, got none")


def doc(body, frontmatter="id: x\ntype: tsd\n"):
    """A temp document. The body follows the closing frontmatter fence."""
    fh = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
    fh.write(f"---\n{frontmatter}---\n{body}")
    fh.close()
    return fh.name


# ---------------------------------------------------------------- local blocks

check("strip_local removes a block and its markers",
      np.strip_local(["keep", np.LOCAL_START, "secret", np.LOCAL_END, "also keep"]),
      ["keep", "also keep"])

check("strip_local keeps a document with no markers",
      np.strip_local(["one", "", "two"]),
      ["one", "", "two"])

check("strip_local removes a block holding a heading",
      np.strip_local([np.LOCAL_START, "# Decision log", "a row", np.LOCAL_END, "# Scope"]),
      ["# Scope"])

check("strip_local removes two separate blocks",
      np.strip_local([np.LOCAL_START, "a", np.LOCAL_END, "keep",
                      np.LOCAL_START, "b", np.LOCAL_END]),
      ["keep"])

# A marker inside a fenced block is documentation of the convention, not a marker.
check("strip_local leaves a fenced example alone",
      np.strip_local(["```markdown", np.LOCAL_START, "x", np.LOCAL_END, "```", "after"]),
      ["```markdown", np.LOCAL_START, "x", np.LOCAL_END, "```", "after"])

# A fence *inside* a local block goes with the block, and its contents must not be
# read as markers while that block is being dropped.
check("strip_local drops a fence nested in a local block",
      np.strip_local([np.LOCAL_START, "```yaml", "a: 1", "```", np.LOCAL_END, "keep"]),
      ["keep"])

refuses("strip_local refuses an unclosed block",
        lambda: np.strip_local(["keep", np.LOCAL_START, "no end here"]))

refuses("strip_local refuses an end with no start",
        lambda: np.strip_local(["keep", np.LOCAL_END]))

refuses("strip_local refuses a nested start, which means one was never closed",
        lambda: np.strip_local([np.LOCAL_START, "a", np.LOCAL_START, "b", np.LOCAL_END]))

# ---------------------------------------------------------------- section depth

check("section_depth is 2 for a title plus ## sections",
      np.section_depth(["# The Title", "", "## Scope", "text", "## Risks"]),
      2)

check("section_depth is 1 for a TSD sectioning at #",
      np.section_depth(["# The Title", "", "# Approval Section", "## Architecture"]),
      1)

check("section_depth ignores a # inside a code fence",
      np.section_depth(["# The Title", "```bash", "# a shell comment", "```", "## Scope"]),
      2)

check("section_depth falls back to 2 when there is only a title",
      np.section_depth(["# The Title", "some prose"]),
      2)

# ---------------------------------------------------------------- preamble region

pre, empty = np.doc_preamble(doc("# Title\n\nIntro prose.\n\n## Scope\n\nbody\n"))
check("doc_preamble returns prose above the first ## section", [l for l in pre if l.strip()],
      ["Intro prose."])
check("doc_preamble reports that region as non-empty", empty, False)

pre, empty = np.doc_preamble(doc("# Title\n\nIntro prose.\n\n# Approval\n\nbody\n"))
check("doc_preamble finds the boundary at # on a TSD-shaped document",
      [l for l in pre if l.strip()], ["Intro prose."])

pre, empty = np.doc_preamble(
    doc(f"# Title\n\n{np.LOCAL_START}\n> **Provenance:** imported.\n{np.LOCAL_END}\n\n## Scope\n"))
check("doc_preamble is empty once the only content is a local block",
      [l for l in pre if l.strip()], [])
check("doc_preamble reports an all-local region as empty", empty, True)

pre, _ = np.doc_preamble(
    doc(f"# Title\n\nKeep me.\n\n{np.LOCAL_START}\n> **Note:** tooling.\n{np.LOCAL_END}\n\n## Scope\n"))
check("doc_preamble keeps prose that sits before a local block",
      [l for l in pre if l.strip()], ["Keep me."])

pre, _ = np.doc_preamble(
    doc(f"# Title\n\n{np.LOCAL_START}\n> **Provenance:** x.\n{np.LOCAL_END}\n\n**Prerequisite:** read 003.\n\n## Scope\n"))
check("doc_preamble keeps prose that sits after a local block",
      [l for l in pre if l.strip()], ["**Prerequisite:** read 003."])

# The old rule took everything after the last blockquote, so a document whose preamble
# ran prose-then-quote reported nothing publishable and @preamble refused.
pre, empty = np.doc_preamble(
    doc(f"# Title\n\n**Implements:** 003.\n\n{np.LOCAL_START}\n> **Template note:** x.\n{np.LOCAL_END}\n\n# Approval\n"))
check("doc_preamble handles prose-then-local, which the blockquote rule could not",
      [l for l in pre if l.strip()], ["**Implements:** 003."])
check("that region is not reported empty", empty, False)

# ---------------------------------------------------------------- repo headings

check("repo_headings lists sections at the derived depth",
      np.repo_headings(doc("# Title\n\n# Approval\n\n## Sub\n\n# Introduction\n")),
      ["# Approval", "# Introduction"])

check("repo_headings excludes a section inside a local block",
      np.repo_headings(doc(f"# Title\n\n## Scope\n\n{np.LOCAL_START}\n## Import notes\nx\n{np.LOCAL_END}\n")),
      ["## Scope"])

# ---------------------------------------------------------------- convert tripwire

np.IMPORTED = False
refuses("convert refuses a bare local marker",
        lambda: np.convert([np.LOCAL_START, "x", np.LOCAL_END]))
refuses("convert refuses a marker on a heading",
        lambda: np.convert([f"## Notes {np.LOCAL_START}"]))
np.IMPORTED = True
refuses("convert refuses a marker in imported prose too",
        lambda: np.convert([np.LOCAL_START, "x"]))
np.IMPORTED = False
np.convert(["```markdown", np.LOCAL_START, "x", np.LOCAL_END, "```"])  # must not refuse

# ------------------------------------------------- obsidian comments

check("strip_comments removes a whole-line comment",
      np.strip_comments(["keep", "%%a note to self%%", "also keep"]),
      ["keep", "", "also keep"])

check("strip_comments removes an inline span and keeps the prose around it",
      np.strip_comments(["Prose %%aside%% continues."]),
      ["Prose  continues."])

check("strip_comments removes two spans on one line",
      np.strip_comments(["a %%one%% b %%two%% c"]),
      ["a  b  c"])

check("strip_comments removes a multi-line comment",
      np.strip_comments(["keep", "%%", "line one", "line two", "%%", "also keep"]),
      ["keep", "", "also keep"])

# The opening line contributes its prose; the closing line contributes nothing, so a
# multi-line comment collapses to one line rather than leaving a stray blank.
check("strip_comments keeps prose before a multi-line comment opens",
      np.strip_comments(["text before %%", "commented", "%%"]),
      ["text before "])

check("strip_comments leaves a fenced example alone",
      np.strip_comments(["```markdown", "%%a note%%", "```", "after"]),
      ["```markdown", "%%a note%%", "```", "after"]) 

check("strip_comments ignores a fence inside a comment",
      np.strip_comments(["%%", "```yaml", "a: 1", "```", "%%", "keep"]),
      ["", "keep"])

refuses("strip_comments refuses an unclosed comment",
        lambda: np.strip_comments(["keep", "%%", "no close"]))

# body_of applies both, so a comment never reaches a comparison or a write.
pre, empty = np.doc_preamble(doc("# Title\n\n%%a question%%\n\n## Scope\n"))
check("doc_preamble drops a comment that is the whole region",
      [l for l in pre if l.strip()], [])
check("that region reports as empty", empty, True)

pre, _ = np.doc_preamble(doc("# Title\n\n%%a question%%\n\n**Implements:** 003.\n\n## Scope\n"))
check("doc_preamble keeps prose and drops the comment beside it",
      [l for l in pre if l.strip()], ["**Implements:** 003."])

np.IMPORTED = False
refuses("convert refuses a stray %% marker",
        lambda: np.convert(["Prose with %%an aside%% in it."]))
np.convert(["```markdown", "%%an example%%", "```"])  # must not refuse

# ------------------------------------------------- inline code vs bold

def seg(text):
    """(content, bold, code) for each rich-text segment of a line."""
    return [(r["text"]["content"], r.get("annotations", {}).get("bold", False),
             r.get("annotations", {}).get("code", False)) for r in np.rich(text)]

# Bold was split off first, so `**` inside a code span was read as a bold marker and
# deleted: `docs/**/openapi.yaml` reached the page as `docs//openapi.yaml`.
check("double asterisks inside inline code survive",
      seg("globs `docs/**/openapi.yaml docs/**/openapi.yml` here"),
      [("globs ", False, False),
       ("docs/**/openapi.yaml docs/**/openapi.yml", False, True),
       (" here", False, False)])

check("inline code alone is still code",
      seg("the `api-standards` repo"),
      [("the ", False, False), ("api-standards", False, True), (" repo", False, False)])

check("bold alone is still bold",
      seg("a **strong** word"),
      [("a ", False, False), ("strong", True, False), (" word", False, False)])

check("bold wrapping inline code keeps both annotations",
      seg("**see `ruleset.yaml` now**"),
      [("see ", True, False), ("ruleset.yaml", True, True), (" now", True, False)])

check("two code spans on one line both survive",
      seg("`a**b` and `c**d`"),
      [("a**b", False, True), (" and ", False, False), ("c**d", False, True)])

# ------------------------------------------------- underline

def useg(text):
    """(content, underline, bold, code) for each rich-text segment of a line."""
    return [(r["text"]["content"], r.get("annotations", {}).get("underline", False),
             r.get("annotations", {}).get("bold", False),
             r.get("annotations", {}).get("code", False)) for r in np.rich(text)]

check("an underline span becomes an underline annotation",
      useg("an ==underlined== word"),
      [("an ", False, False, False), ("underlined", True, False, False),
       (" word", False, False, False)])

check("bold wrapping an underline keeps both annotations",
      useg("**see ==this== now**"),
      [("see ", False, True, False), ("this", True, True, False),
       (" now", False, True, False)])

# A code span holding the delimiter must not be read as an underline run, the same
# failure double asterisks had inside inline code.
check("equals signs inside inline code survive",
      useg("compare `a==b` here"),
      [("compare ", False, False, False), ("a==b", False, False, True),
       (" here", False, False, False)])

check("a lone equals pair is not an underline",
      useg("x == y"), [("x == y", False, False, False)])

check("underline no longer counts as an unrepresentable construct",
      np.loss_reasons([{"type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "plain_text": "a",
                                                 "annotations": {"underline": True}}]}}]),
      [])

check("block colour is still an unrepresentable construct",
      np.loss_reasons([{"type": "paragraph",
                    "paragraph": {"rich_text": [], "color": "red"}}]),
      ["block colour"])

# ---------------------------------------------------------------- report

# The checks above run at import, which is how this file has always worked. Discovery
# imports it and would otherwise collect nothing from it, so a whole suite of the
# functions that decide what leaves the repository would pass unrun for anyone who
# trusts `unittest discover`. The case below carries them into that run.


class ParserChecks(unittest.TestCase):
    def test_every_check_passes(self):
        self.assertEqual(FAILED, [], "\n\n" + "\n\n".join(FAILED))


if __name__ == "__main__":
    if FAILED:
        print(f"{len(FAILED)} failure(s):\n")
        for f in FAILED:
            print(f"  {f}\n")
        sys.exit(1)
    print(f"all {CHECKS} checks pass")
