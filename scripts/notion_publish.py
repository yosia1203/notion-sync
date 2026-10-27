#!/usr/bin/env python3
"""Maintain an existing Notion page from this repository's copy of a document.

    verify   <doc.md>                         compare the whole managed body
             add --with-comments to also list the page's open threads
    manual   <doc.md>                         classify every region, read-only
    coverage <doc.md>                         page blocks that no section accounts for
    plan     <doc.md> "<heading>"             show a region's write without making it
    apply    <doc.md> "<heading>"             replace that region
    insert   <doc.md> "<heading>" "<before>"  create a missing section
    drop     <doc.md> "<heading>"             remove a section the repository no longer has
    comments <doc.md>                         every thread on the page, as a work list
    reply    <doc.md> "<discussion>" "<text>"  answer one thread

    --preamble            replace only the selected section's introduction
    --allow-flatten       permit a reported lossy conversion, never a nested page or database
    --allow-link-loss     permit reported unresolved or removed links for this write
    --allow-comment-edit  rewrite a commented block; a phrase thread on it leaves the page
    --with-comments       verify also reads every open thread; slow on a long page
    --yes                 execute the previewed insert, drop or reply

Resolving a thread is not in this list because the public API cannot do it, and it does
not return resolved threads either. Resolving stays a hand action in Notion; `comments`
lists what is still open, and a resolved thread simply leaves that list.

The heading `@preamble` selects the prose between the body title and the first section.
A repeated subsection name needs its parent path, as in "## Parent > ### Details".

Section by section, because the whole body of a long RFC is too much to verify in
one write. `plan` converts the markdown and prints the blocks it would create,
alongside the page blocks it would delete, and writes nothing.

`apply` works block by block. It matches the converted blocks to the page's the way a
text diff matches lines: an identical block is left alone, a reworded block of the same
type is rewritten through its own ID, and only blocks with no match are created or
deleted. Every block that survives keeps its ID and its comment threads.

Only the markdown this repository actually uses is converted: headings, paragraphs,
bullet, numbered and to-do lists, fenced code, tables, quotes, callouts, dividers, and
plain toggle blocks marked with `<!-- toggle-block: title -->` ... `<!-- toggle-block:end -->`,
with bold, inline code and links inside text. Anything unrecognised stops the run rather
than being dropped silently.
"""

import copy, difflib, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
from functools import wraps

TOKEN = os.path.expanduser("~/.config/notion-sync/notion_token.json")
VERSION = "2022-06-28"
LIMIT = 2000
LANGUAGES = {"yaml": "yaml", "json": "json", "go": "go", "bash": "bash",
             "shell": "shell", "text": "plain text", "plain text": "plain text",
             "mermaid": "mermaid", "makefile": "makefile", "diff": "diff",
             "markdown": "markdown", "javascript": "javascript"}


def api_version(path, body):
    """Only the first-position append needs the newer API version. Every other request
    stays on the pinned one, so a version bump is a deliberate change rather than
    something a new field drags in."""
    if body and "position" in body and path.endswith("/children"):
        return "2026-03-11"
    return VERSION


def api(path, method="GET", body=None, soft=False):
    tok = json.loads(Path(TOKEN).read_text())["access_token"]
    for attempt in range(4):
        req = urllib.request.Request(
            f"https://api.notion.com/v1{path}",
            data=json.dumps(body).encode() if body else None, method=method,
            headers={"Authorization": f"Bearer {tok}",
                     "Notion-Version": api_version(path, body),
                     "Content-Type": "application/json",
                     "User-Agent": "notion-sync/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:400]
            retryable = e.code in (429, 529) or (
                method in ("GET", "DELETE") and e.code in (500, 502, 503, 504))
            if retryable and attempt < 3:
                delay = max(0, float(e.headers.get("Retry-After", 2 ** attempt)))
                print(f"  rate/server limit: waiting {delay:g}s before retry", flush=True)
                time.sleep(delay)
                continue
            if soft:
                return {"_error": e.code, "_detail": detail,
                        "_uncertain": method not in ("GET", "DELETE") and e.code >= 500}
            sys.exit(f"{method} {path} failed ({e.code}): {detail}")
        except (urllib.error.URLError, TimeoutError) as e:
            if method not in ("GET", "DELETE"):
                message = (f"{method} {path}: response lost; write outcome unknown. "
                           "Do not retry automatically. Run verify and inspect the page.")
                if soft:
                    return {"_error": "transport", "_detail": message, "_uncertain": True}
                sys.exit(message)
            if attempt == 3:
                sys.exit(f"{method} {path} failed after 4 attempts: {e}")
            time.sleep(2 * (attempt + 1))


# ---------- markdown -> rich text ----------

BOLD_RE = re.compile(r"(\*\*.+?\*\*)")
CODE_RE = re.compile(r"`[^`]+`")
INNER_RE = re.compile(r"(~~.+?~~|==.+?==|`[^`]+`|\*[^*\n]+\*|\[[^\]]+\]\([^)]+\)|<https?://[^>]+>)")

BASE_DIR = None          # directory of the document being published
PREAMBLE = False         # sync only the lines before the first subsection
ALLOW_FLATTEN = False    # publish even where a block type cannot be reproduced
ALLOW_LINK_LOSS = False  # publish even where the page loses a link
ALLOW_COMMENT_EDIT = False   # rewrite a commented block; whole-block threads survive
WITH_COMMENTS = False    # verify also reports the page's open threads
UNRESOLVED = []          # cross-document links with no page to point at
UNCLAIMED_SHOWN = 12     # unclaimed blocks listed in full before the report summarises


def published_url_of(rel):
    """A repo path resolves to nothing in Notion. Use the target document's own
    published_url so a cross-reference stays a working link instead of going flat.

    Two different absences hide behind a missing URL. A target that carries the field
    and leaves it empty is waiting for its page, and pointing at it now would publish a
    dead reference: that is the blocker this guard exists for. A target with no such
    field is a document that will never be published, so no page will ever exist
    to point at. Reporting that one would
    fail verification permanently and teach the next person to ignore the result, so
    the link flattens to plain text and the document can still pass."""
    target = os.path.normpath(os.path.join(BASE_DIR, rel.split("#")[0]))
    if not os.path.isfile(target):
        UNRESOLVED.append(rel); return None
    parts = Path(target).read_text().split("---\n", 2)
    front = parts[1] if len(parts) > 2 else ""
    m = re.search(r"^published_url:[ \t]*(\S+)$", front, re.M)
    if m:
        return m.group(1)
    if re.search(r"^published_url:", front, re.M):
        UNRESOLVED.append(rel)
    return None


def _segments(text, ann):
    """Text inside a bold span or outside one, split into code, italic and links.
    Annotations combine: `bold + code` is two of the spans on these pages, and a flat
    parser turns it into bold text with literal backticks in it."""
    out = []
    for part in INNER_RE.split(text):
        if not part:
            continue
        a, href, content = dict(ann), None, part
        if part.startswith("~~") and part.endswith("~~") and len(part) > 4:
            a["strikethrough"], content = True, part[2:-2]
        elif part.startswith("==") and part.endswith("==") and len(part) > 4:
            a["underline"], content = True, part[2:-2]
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            a["code"], content = True, part[1:-1]
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            a["italic"], content = True, part[1:-1]
        elif part.startswith("[") and "](" in part:
            content, href = re.match(r"\[([^\]]+)\]\(([^)]+)\)", part).groups()
            if not href.startswith("http"):
                href = published_url_of(href)
        elif part.startswith("<http"):
            content = href = part[1:-1]
        item = {"type": "text", "text": {"content": content}}
        if href:
            item["text"]["link"] = {"url": href}
        if a:
            item["annotations"] = a
        out.append(item)
    return out


def rich(text):
    """Inline code is hidden from the bold split, then restored.

    Splitting on `**` first meant a code span holding two of them had its middle read as
    a bold run: `docs/**/openapi.yaml docs/**/openapi.yml` came out as three plain
    segments with the asterisks deleted and no code annotation, so the glob published
    wrong and nothing reported it. Masking keeps `**` inside backticks out of the split
    while leaving bold that *wraps* a code span working, which two spans on these pages
    rely on."""
    spans = []

    def stash(m):
        spans.append(m.group(0))
        return f"\x00{len(spans) - 1}\x00"

    def restore(s):
        return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], s)

    masked = CODE_RE.sub(stash, text)
    out = []
    for part in BOLD_RE.split(masked):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            out.extend(_segments(restore(part[2:-2]), {"bold": True}))
        else:
            out.extend(_segments(restore(part), {}))
    split = []
    for item in out:
        content = item["text"]["content"]
        for start in range(0, max(1, len(content)), LIMIT):
            piece = copy.deepcopy(item)
            piece["text"]["content"] = content[start:start + LIMIT]
            split.append(piece)
    return split or [{"type": "text", "text": {"content": ""}}]


def callout_icon(text):
    """A blockquote opening with an emoji is a callout, which is content this repository
    authors. A blockquote without one is a quotation, whose wording is preserved. Lint
    shares this test, so its em dash check skips the quotation and still reads the callout.

    An emoji can be several codepoints: the warning sign carries a variation selector, so
    matching a single character misses it."""
    return re.match(r"^([^\w\s\[`*]{1,4})\s+(.*)$", text)


def para(kind, text):
    return {"object": "block", "type": kind, kind: {"rich_text": rich(text)}}


TOGGLE_MARK = "<!-- toggle -->"
MANUAL_MARK = "<!-- manual -->"   # on the page, compared, never written by the tool
LOCAL_MARK = "<!-- local:"      # the pair below, matched loosely for the convert tripwire
LOCAL_START = "<!-- local:start -->"
LOCAL_END = "<!-- local:end -->"
# A plain Notion toggle block, not a toggle heading: the lines between the pair become
# its children. Markdown has no toggle, so the repository marks one the way it marks a
# toggle heading or a local block.
TOGGLE_BLOCK_RE = re.compile(r"^<!-- toggle-block: (.+?) -->$")
TOGGLE_BLOCK_END = "<!-- toggle-block:end -->"
IMPORTED = False        # set from the document's frontmatter before anything converts


def literal(l, what, fix):
    """A construct that reaches the page as literal characters. Refuse it in prose we
    author. In imported prose it is the authors' text, which this tool must not
    correct, so publish it as it stands and say so: the defect belongs in that
    document's Import notes, not in an edit."""
    if IMPORTED:
        print(f"  LITERAL ON THE PAGE: {l.strip()[:60]!r} ({what}). Imported prose, "
              "published unchanged. Record it in the document's Import notes.")
        return
    sys.exit(f"REFUSING: {l.strip()[:60]!r} {what}. {fix}")


def convert(md_lines):
    """Markdown lines -> Notion block payloads."""
    # Validate every prose line before folding paragraphs or list continuations.
    for line in mask_fences(md_lines):
        if LOCAL_MARK in line or "%%" in line:
            sys.exit("REFUSING: local/comment marker reached the converter.")
        if re.match(r"^\s+(`{3,}|~{3,})", line):
            literal(line, "is an indented code fence, which this converter does not read",
                    "Unindent the fence, or give the section to the page as manual content.")
        if re.search(r"!\[[^\]]*\]\(", line):
            sys.exit("REFUSING: images require a manual section; this converter cannot upload them.")
        if re.search(r"\[\^[\w-]+\]", line):
            literal(line, "carries a footnote marker", "Put the aside in References.")
        if re.search(r"</?(details|summary|br|img|div|span|callout|columns|u|b|i)\b", line):
            literal(line, "contains unsupported HTML", "Remove it or use a manual section.")
    blocks, i, list_stack = [], 0, []
    def append_list(item, indent):
        while list_stack and list_stack[-1][0] >= indent:
            list_stack.pop()
        if indent:
            if not list_stack:
                sys.exit("REFUSING: indented list item has no parent.")
            parent = list_stack[-1][1]
            parent[parent["type"]].setdefault("children", []).append(item)
        else:
            blocks.append(item)
        list_stack.append((indent, item))
    while i < len(md_lines):
        l = md_lines[i]
        if not l.strip():
            i += 1; continue
        if not re.match(r"^\s*([-*]|\d+\.)\s", l):
            list_stack.clear()
        fence = fence_open(l)
        if fence:
            lang = l[len(fence):].strip() or "plain text"
            if lang not in LANGUAGES:
                sys.exit(f"REFUSING: unmapped code language {lang!r}; add an explicit language mapping.")
            j = i + 1
            body = []
            while j < len(md_lines) and not fence_close(md_lines[j], fence):
                body.append(md_lines[j]); j += 1
            if j == len(md_lines):
                sys.exit("REFUSING: unclosed code fence.")
            code = "\n".join(body)
            # The 2000-char cap is per rich-text object, not per block: a long sample
            # splits across several objects inside the one code block.
            pieces = [code[n:n + LIMIT] for n in range(0, len(code), LIMIT)] or [""]
            blocks.append({"object": "block", "type": "code", "code": {
                "rich_text": [{"type": "text", "text": {"content": c}} for c in pieces],
                "language": LANGUAGES[lang]}})
            i = j + 1; continue
        # A local block is content this repository keeps. Unlike the constructs below it
        # is not a rendering problem, so it does not go through literal(): the imported
        # exemption there prints and publishes, and 003 and 004 carry Import notes that
        # must never reach a page. Refuse unconditionally. A marker inside a code fence
        # is an example and never gets here, the branch above having consumed it.
        if "%%" in l:
            sys.exit(f"REFUSING: {l.strip()[:60]!r} carries an Obsidian comment, which is "
                     "this repository's own margin note and never reaches a page.")
        if LOCAL_MARK in l:
            sys.exit(f"REFUSING: {l.strip()[:60]!r} is a local block marker, and the content "
                     "it delimits never reaches Notion. Publish a section that does not "
                     "contain one, or move the local block out of it.")
        m = TOGGLE_BLOCK_RE.match(l.strip())
        if l.strip() == TOGGLE_BLOCK_END:
            sys.exit(f"REFUSING: {TOGGLE_BLOCK_END} with no <!-- toggle-block: title --> before it.")
        if m:
            # Fences are tracked so a marker or a `#` inside a code sample stays code.
            j, fence, inner = i + 1, None, []
            while j < len(md_lines):
                line = md_lines[j]
                if fence:
                    if fence_close(line, fence):
                        fence = None
                elif fence_open(line):
                    fence = fence_open(line)
                elif line.strip() == TOGGLE_BLOCK_END:
                    break
                elif TOGGLE_BLOCK_RE.match(line.strip()):
                    sys.exit("REFUSING: a toggle block opens inside another toggle block. "
                             "Close the first one, or keep the content in a single toggle.")
                elif re.match(r"^#{1,6} ", line):
                    # A heading inside a toggle would move a section out of the page's
                    # heading order, which every section comparison relies on.
                    sys.exit(f"REFUSING: {line.strip()[:60]!r} is a heading inside a toggle block. "
                             "Use a bold lead-in, or close the toggle before the heading.")
                inner.append(line)
                j += 1
            if j == len(md_lines):
                sys.exit(f"REFUSING: {l.strip()[:60]!r} is never closed with {TOGGLE_BLOCK_END}.")
            blocks.append({"object": "block", "type": "toggle", "toggle": {
                "rich_text": rich(m.group(1)), "children": convert(inner)}})
            i = j + 1; continue
        if re.match(r"^\|", l):                       # table
            rows = []
            while i < len(md_lines) and md_lines[i].startswith("|"):
                if not re.match(r"^\|[ \-:|]+\|$", md_lines[i]):
                    row = md_lines[i].strip()[1:]
                    if row.endswith("|"):
                        row = row[:-1]
                    rows.append([c.strip().replace(r"\|", "|") for c in re.split(r"(?<!\\)\|", row)])
                i += 1
            width = max(len(r) for r in rows)
            blocks.append({"object": "block", "type": "table", "table": {
                "table_width": width, "has_column_header": True, "has_row_header": False,
                "children": [{"object": "block", "type": "table_row", "table_row": {
                    "cells": [rich(c) for c in (r + [""] * (width - len(r)))]}} for r in rows]}})
            continue
        if re.match(r"^#{1,4} ", l):
            marks, text = l.split(" ", 1)
            level = f"heading_{len(marks)}"
            if MANUAL_MARK in text:
                sys.exit("REFUSING: a manual subsection cannot be replaced through its parent.")
            if TOGGLE_MARK in text:
                # A toggleable heading holds its section as children, up to the next
                # heading at the same or a higher level. Fences are masked so a `#` in a
                # code sample does not end the section early.
                rest = mask_fences(md_lines[i + 1:])
                end = next((k for k, ml in enumerate(rest)
                            if re.match(r"^#{1,%d} " % len(marks), ml)), len(rest))
                block = para(level, strip_mark(text))
                block[level]["is_toggleable"] = True
                block[level]["children"] = convert(md_lines[i + 1:i + 1 + end])
                blocks.append(block); i += 1 + end; continue
            blocks.append(para(level, text)); i += 1; continue
        if l.strip() == "---":
            blocks.append({"object": "block", "type": "divider", "divider": {}}); i += 1; continue
        if l.startswith("> "):
            text = l[2:]
            i += 1
            while i < len(md_lines) and md_lines[i].startswith("> "):
                text += " " + md_lines[i][2:].strip(); i += 1
            m = callout_icon(text)
            if m:
                icon, ctext = m.groups()
                blocks.append({"object": "block", "type": "callout", "callout": {
                    "rich_text": rich(ctext), "icon": {"type": "emoji", "emoji": icon}}})
            else:
                blocks.append(para("quote", text))
            continue
        m = re.match(r"^\s*[-*]\s+\[([ xX])\]\s+(.*)$", l)
        if m:
            mark, text = m.groups()
            i += 1
            while i < len(md_lines) and md_lines[i].startswith("  ") and md_lines[i].strip() \
                    and not re.match(r"^\s*([-*]|\d+\.)\s", md_lines[i]):
                text += " " + md_lines[i].strip(); i += 1
            append_list({"object": "block", "type": "to_do", "to_do": {
                "rich_text": rich(text), "checked": mark in "xX"}}, len(l) - len(l.lstrip()))
            continue
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", l)
        if m:
            indent, marker, text = m.groups()
            kind = "numbered_list_item" if marker[0].isdigit() else "bulleted_list_item"
            i += 1
            while i < len(md_lines) and md_lines[i].startswith("  ") and md_lines[i].strip() \
                    and not re.match(r"^\s*([-*]|\d+\.)\s", md_lines[i]):
                text += " " + md_lines[i].strip(); i += 1
            append_list(para(kind, text), len(indent)); continue
        # These parse as a paragraph and reach the page as literal characters. Authored
        # prose should never contain them; refusing here is what makes the rule mechanical
        # rather than something the page tells you about afterwards.
        if re.match(r"^#{5,} ", l):
            literal(l, "is a fifth-level or deeper heading, which Notion does not support",
                    "Use a heading within four levels or a bold lead-in.")
        if re.search(r"\[\^[\w-]+\]", l):
            literal(l, "carries a footnote marker",
                    "Put the aside in the sentence or in References.")
        if re.search(r"</?(details|summary|br|img|div|span|callout|columns|u|b|i)\b", l):
            literal(l, "contains inline HTML, which Notion renders none of",
                    "Take it out.")
        text = l
        i += 1
        while i < len(md_lines) and md_lines[i].strip() and not re.match(
                r"^(```|\||#{1,6} |> |\s*([-*]|\d+\.)\s|---$|<!-- toggle-block)", md_lines[i]):
            text += " " + md_lines[i].strip(); i += 1
        blocks.append(para("paragraph", text))
    return blocks


# ---------- page side ----------

def page_id(path):
    m = re.search(r"^published_url:[ \t]*(\S+)$", Path(path).read_text(), re.M)
    if not m:
        sys.exit(f"{path} has no published_url, so there is no page to compare against. "
                 "Import it first (see SKILL.md), then record the URL the import produces.")
    seg = re.sub(r"[^0-9a-zA-Z]", "", urllib.parse.urlparse(m.group(1)).path)[-32:]
    return seg


def top_children(pid):
    out, cur = [], None
    while True:
        q = "?page_size=100" + (f"&start_cursor={cur}" if cur else "")
        j = api(f"/blocks/{pid}/children{q}")
        out.extend(j["results"])
        if not j.get("has_more"):
            return out
        cur = j["next_cursor"]


def read_snapshot(fn):
    """Reuse each paginated read during one report; never cache across commands."""
    @wraps(fn)
    def run(*args, **kwargs):
        global top_children
        fetch, cache = top_children, {}
        def cached(pid):
            if pid not in cache:
                cache[pid] = fetch(pid)
            return copy.deepcopy(cache[pid])
        top_children = cached
        try:
            return fn(*args, **kwargs)
        finally:
            top_children = fetch
    return run


def text_of(b):
    rt = b.get(b["type"], {}).get("rich_text") or []
    return "".join(t["plain_text"] for t in rt)


def strip_mark(s):
    return s.replace(TOGGLE_MARK, "").replace(MANUAL_MARK, "").strip()


def norm(s):
    return re.sub(r"[^a-z0-9]", "", strip_mark(s).lower())


def selector_parts(heading):
    return [norm(part) for part in heading.split(" > ")]


def selector_matches(actual, requested):
    a, r = selector_parts(actual), selector_parts(requested)
    return len(a) >= len(r) and a[-len(r):] == r


def leaf_heading(heading):
    return heading.rsplit(" > ", 1)[-1]


def repo_heading_entries(path):
    raw, masked = body_of(path)
    heads = [i for i, line in enumerate(masked) if re.match(r"^#{1,4} ", line)]
    stack, out = [], []
    for i in heads[1:]:
        h = raw[i].strip()
        depth = len(h) - len(h.lstrip("#"))
        while stack and stack[-1][0] >= depth:
            stack.pop()
        stack.append((depth, h))
        out.append((i, " > ".join(title for _, title in stack)))
    return raw, masked, out


def repo_section(path, heading):
    raw, masked, entries = repo_heading_entries(path)
    matches = [(i, h) for i, h in entries if selector_matches(h, heading)]
    if len(matches) > 1:
        sys.exit(f"REFUSING: ambiguous repository heading {heading!r}; qualify it with its parent.")
    if not matches:
        sys.exit(f"section {heading!r} not found in {path}")
    i, _ = matches[0]
    depth = len(raw[i]) - len(raw[i].lstrip("#"))
    end = next((j for j in range(i + 1, len(masked))
                if re.match(r"^#{1,%d} " % depth, masked[j])), len(raw))
    return raw[i], raw[i + 1:end]


def render(block, live):
    """One block as text lines, so the two sides can be diffed like prose."""
    t = block["type"]
    body = block[t]
    def txt(rt, marked=False):
        """Render segments back to markdown when comparing, so a bold or code span
        moving inside a sentence is a difference rather than an identical line."""
        out = []
        for x in (rt or []):
            s = x.get("plain_text") if live else x["text"]["content"]
            if marked:
                href = x.get("href") if live else (x.get("text", {}).get("link") or {}).get("url")
                if href:
                    s = f"[{s}]({href})"
                a = x.get("annotations", {}) or {}
                if a.get("strikethrough"):
                    s = f"~~{s}~~"
                if a.get("code"):
                    s = f"`{s}`"
                if a.get("italic"):
                    s = f"*{s}*"
                if a.get("bold"):
                    s = f"**{s}**"
            out.append(s)
        return "".join(out)
    if t == "divider":
        return ["---"]
    if t == "code":
        lang = body.get("language", "")
        return [f"```{lang}"] + txt(body.get("rich_text")).split("\n") + ["```"]
    if t == "table":
        rows = body.get("children")
        if rows is None:
            return ["(table)"]
        return [" | ".join(txt(c, True) for c in r["table_row"]["cells"]) for r in rows]
    if t == "table_row":
        return [" | ".join(txt(c, True) for c in body["cells"])]
    if t == "callout":
        # A distinct prefix, used only for comparison: a callout and a quote whose text
        # starts with the same emoji render identically otherwise, and the difference
        # between them is exactly what a republish is meant to restore.
        icon = (body.get("icon") or {}).get("emoji", "")
        return [">! " + (icon + " " if icon else "") + txt(body.get("rich_text"), True)]
    if t == "child_page":
        return []          # a nested page is not this document's content
    if t == "to_do":
        return [("- [x] " if body.get("checked") else "- [ ] ") + txt(body.get("rich_text"), True)]
    prefix = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### ", "heading_4": "#### ",
              "bulleted_list_item": "- ", "numbered_list_item": "1. ", "quote": "> ",
              "toggle": "▸ "}.get(t, "")
    return [prefix + txt(body.get("rich_text"), True)]


def lines_of(blocks, live):
    out = []
    for b in blocks:
        out.extend(render(b, live))
        if b["type"] != "table":
            out.extend("  " + line for line in lines_of(b[b["type"]].get("children", []), live))
    return out


def show_diff(doomed, new_blocks, pid_fetch):
    before = []
    for b in doomed:
        if b["type"] == "table" and b.get("has_children"):
            # Render these rows through the same marked path as every other block, or
            # inline code in a table cell reads as a difference on every comparison.
            for r in pid_fetch(b["id"]):
                before.extend(render(r, True))
        else:
            before.extend(lines_of([b], True))
    after = lines_of(new_blocks, False)
    diff = list(difflib.unified_diff(before, after, "page", "repo", lineterm="", n=1))
    if not diff:
        print("  no textual difference (structural comparison also runs)")
        return False
    print(f"\n  diff ({sum(1 for l in diff if l.startswith('-') and not l.startswith('---'))} removed, "
          f"{sum(1 for l in diff if l.startswith('+') and not l.startswith('+++'))} added):")
    for l in diff:
        print("   " + l[:150])
    return True


def find_heading(kids, title):
    matches = [i for i, b in enumerate(kids)
               if b["type"].startswith("heading") and norm(text_of(b)) == norm(title)]
    if len(matches) > 1:
        sys.exit(f"REFUSING: ambiguous page heading {title!r}; {len(matches)} matches.")
    return matches[0] if matches else None


def span(kids, idx):
    """The heading at idx plus everything under it, to the next equal-or-higher heading."""
    rank = {"heading_1": 1, "heading_2": 2, "heading_3": 3, "heading_4": 4}
    here = rank.get(kids[idx]["type"], 3)
    end = next((j for j in range(idx + 1, len(kids))
                if rank.get(kids[j]["type"], 9) <= here), len(kids))
    return kids[idx:end]


def assert_toggle_section_clean(kids, idx):
    """A toggle heading owns its whole section, nested inside it. Blocks sitting beside
    the heading, before the next equal-or-higher heading, are a second copy of that
    section: writing into the toggle leaves them on the page, unreferenced and stale.
    Only a whole-document verify reports them, so refuse the write here instead."""
    stray = [b for b in span(kids, idx)[1:]
             if b["type"] not in ("child_page", "child_database")]
    if stray:
        first = stray[0]
        rt = first.get(first["type"], {}).get("rich_text") or []
        sample = "".join(t.get("plain_text") or t.get("text", {}).get("content", "")
                         for t in rt)[:60] or first["type"]
        sys.exit(f"REFUSING: the page holds this section both inside the toggle and beside it "
                 f"({len(stray)} block(s) after the heading, first: {sample!r}). Writing would "
                 f"orphan that copy. Remove it from the page, then sync.")


def cmd_drop(path, title, confirmed):
    if any(selector_matches(h, title) for h, _, _ in repo_regions(path)):
        sys.exit(f"REFUSING: {title!r} is still publishable in {path}.")
    pid = page_id(path)
    kids = top_children(pid)
    hit = locate(kids, title, pid)
    if not hit:
        sys.exit(f"{title!r} is not on the page.")
    siblings, idx, container = hit
    doomed = hydrated([b for b in span(siblings, idx)
                       if b["type"] not in ("child_page", "child_database")])
    print(f"drop {title!r}: {len(doomed)} blocks")
    if not confirmed:
        print("dry run. Pass --yes to delete.")
        return
    replace_blocks(container, doomed, [])


def cmd_insert(path, title, before, confirmed):
    global BASE_DIR
    BASE_DIR = os.path.dirname(os.path.abspath(path))
    pid = page_id(path)
    kids = top_children(pid)
    if locate(kids, title, pid):
        sys.exit(f"REFUSING: {title!r} is already on the page.")
    hit = locate(kids, before, pid)
    if not hit:
        sys.exit(f"anchor {before!r} not found.")
    siblings, at, container = hit
    if at == 0:
        sys.exit("REFUSING: no preceding block to anchor this insert. Add the first block by hand.")
    _, masked = body_of(path)
    depth = len(title) - len(title.lstrip("#")) if title.startswith("#") else section_depth(masked)
    heading, body = repo_section(path, "#" * depth + " " + title.lstrip("# "))
    assert_writable_region(path, heading)
    # Converting the heading line with its body nests a toggle section inside its heading.
    blocks = convert([leaf_heading(heading)] + body)
    validate_payload(blocks)
    print(f"insert {title!r}: {len(blocks)} blocks")
    if not confirmed:
        print("dry run. Pass --yes to write.")
        return
    replace_blocks(container, [], blocks, siblings[at - 1]["id"])


def strip_local(lines):
    """Drop every local block, markers included. A local block is content this
    repository keeps: the record of an import, a note on how the tooling behaves, a
    question left for the next session. SKILL.md carries the convention.

    A marker inside a fenced block is an example of the convention rather than a
    marker, so fences are tracked. A fence *inside* a local block goes with the block,
    and its lines are not read as markers on the way out.

    Refuses rather than guesses. An unclosed block would make the rest of the file
    local, which stops content publishing instead of leaking it, but silently; a
    second start before the first close is the same mistake seen earlier."""
    out, in_fence, local = [], None, False
    for l in lines:
        if in_fence:
            if not local:
                out.append(l)
            if fence_close(l, in_fence):
                in_fence = None
            continue
        if fence_open(l):
            in_fence = fence_open(l)
            if not local:
                out.append(l)
            continue
        if not in_fence:
            if LOCAL_START in l:
                if local:
                    sys.exit(f"REFUSING: a second {LOCAL_START} opens before the first one "
                             f"closes, so a block is missing its {LOCAL_END}.")
                local = True
                continue
            if LOCAL_END in l:
                if not local:
                    sys.exit(f"REFUSING: {LOCAL_END} with no {LOCAL_START} before it.")
                local = False
                continue
        if not local:
            out.append(l)
    if local:
        sys.exit(f"REFUSING: an unclosed {LOCAL_START} would make the rest of the file "
                 "local. Close it.")
    return out


def strip_comments(lines):
    """Drop Obsidian comments, `%%like this%%`. They are the repository's own margin
    notes: invisible in Obsidian's reading view, and nothing a Notion reader should see.

    Treated as local in their own right rather than needing a local block around them.
    Requiring the wrapper was redundant, and forgetting it put a note-to-self in the
    publishable region where nothing would have caught it.

    Three shapes: a span inside a line, a whole line, and a multi-line block opened and
    closed by a bare `%%`. Prose either side of a span survives; a line that was only a
    comment becomes empty, which keeps the paragraph breaks around it intact. A comment
    inside a fenced block is an example of the syntax and is left alone."""
    out, in_fence, in_comment = [], None, False
    for l in lines:
        if in_fence:
            out.append(l)
            if fence_close(l, in_fence):
                in_fence = None
            continue
        if in_comment:
            if "%%" in l:
                in_comment = False
                rest = l.split("%%", 1)[1]
                if rest.strip():
                    out.append(rest)
            continue
        if fence_open(l):
            in_fence = fence_open(l)
            out.append(l)
            continue
        while l.count("%%") >= 2:
            a = l.index("%%")
            b = l.index("%%", a + 2)
            l = l[:a] + l[b + 2:]
        if "%%" in l:
            in_comment = True
            head = l.split("%%", 1)[0]
            out.append(head)
            continue
        out.append(l)
    if in_comment:
        sys.exit("REFUSING: an unclosed %% would comment out the rest of the file.")
    return out


def fence_open(line):
    m = re.match(r"^(`{3,}|~{3,})(.*)$", line)
    return m.group(1) if m else None


def fence_close(line, fence):
    return bool(re.fullmatch(re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line))


def mask_fences(lines, strict=True):
    """Fenced blocks blanked, so a `#` in a shell comment is not read as a heading.

    `strict=False` returns what it read instead of refusing an unclosed fence, so lint
    can share this reader and report the defect as one finding among others."""
    out, fence = [], None
    for line in lines:
        if fence:
            out.append("")
            if fence_close(line, fence):
                fence = None
        elif fence_open(line):
            fence = fence_open(line)
            out.append("")
        else:
            out.append(line)
    if fence and strict:
        sys.exit("REFUSING: unclosed code fence.")
    return out


def body_of(path):
    """The document body with frontmatter and local blocks gone, as (raw, masked)."""
    raw = strip_comments(strip_local(Path(path).read_text().split("---\n", 2)[2].split("\n")))
    return raw, mask_fences(raw)


def section_depth(masked):
    """The depth at which this document's top-level sections sit. Both shapes in the
    repository are correct: an RFC or squad doc carries one H1 as its title and sections
    at `##`, and a TSD sections at `#` because its company template says so. So the
    depth is read off the document, never assumed. The first heading is the title where
    there is one, which makes the second heading the first section."""
    lines = mask_fences(masked)
    depths = [len(l) - len(l.lstrip("#")) for l in lines if re.match(r"^#{1,4} ", l)]
    return depths[1] if len(depths) > 1 else 2


def repo_headings(path, depth=None):
    """This document's section headings, in order, at its own section depth.

    The document's first heading is its title and is never a section, which matters on
    a TSD: there the title and the sections sit at the same depth, so filtering by
    depth alone returns the title as though it were a section."""
    raw, masked = body_of(path)
    d = depth if depth else section_depth(masked)
    heads = [i for i, l in enumerate(masked) if re.match(r"^#{1,4} ", l)]
    return [raw[i].strip() for i in heads[1:] if re.match(r"^#{%d} " % d, masked[i])]


def doc_preamble(path):
    """The lines between the title and the first section, and whether anything
    publishable is left in them.

    A document may legitimately carry publishable prose here. The templates are the
    company's and nothing in this repository can forbid a headingless top region, so
    this is a permanent state rather than one to migrate away.

    The boundary is the document's own section depth, not a fixed `## `. The earlier
    rule also treated everything after the last blockquote in the region as the
    publishable part, which read a provenance note correctly and read a document whose
    preamble ran prose-then-note backwards, reporting nothing publishable. Local blocks
    now carry that meaning explicitly, so position decides nothing."""
    raw, masked = body_of(path)
    heads = [i for i, l in enumerate(masked) if re.match(r"^#{1,4} ", l)]
    if not heads:
        return raw, not any(l.strip() for l in raw)
    d = section_depth(masked)
    first = next((i for i in heads[1:] if re.match(r"^#{%d} " % d, masked[i])), len(masked))
    body = raw[heads[0] + 1:first]
    return body, not any(l.strip() for l in body)


def section_blocks(kids, idx, preamble=False):
    """The page blocks belonging to the heading at idx. A toggle heading keeps them
    inside itself; every other heading has them as siblings. Nested pages are never
    part of a section: deleting one trashes the page."""
    rank = {"heading_1": 1, "heading_2": 2, "heading_3": 3, "heading_4": 4}
    here = rank.get(kids[idx]["type"], 3)
    if kids[idx].get("has_children"):
        blocks = top_children(kids[idx]["id"])
    else:
        end = next((j for j in range(idx + 1, len(kids))
                    if rank.get(kids[j]["type"], 9) <= (4 if preamble else here)), len(kids))
        blocks = kids[idx + 1:end]
    return [b for b in blocks if b["type"] not in ("child_page", "child_database")]


def rendered_page_lines(blocks):
    out = []
    for b in blocks:
        if b["type"] == "table" and b.get("has_children"):
            for r in top_children(b["id"]):
                out.extend(render(r, True))
        else:
            out.extend(render(b, True))
    return out


def cmd_coverage(path):
    """Which page blocks no section accounts for. Section-by-section checks cannot see
    a range nothing claims, which is how 006's document intro stayed frozen while every
    section reported clean."""
    pid = page_id(path)
    kids = top_children(pid)
    body, _ = doc_preamble(path)
    raw, masked = body_of(path)
    repo_heads = {norm(l.lstrip("# ")) for l in masked if re.match(r"^#{1,4} ", l)}
    # The region above the first section is whatever no section claims. Stopping at the
    # first heading_2 assumed every document sections at `##`, which counted both of a
    # TSD's leading heading_1 sections as preamble.
    section_heads = {norm(h.lstrip("# ")) for h in repo_headings(path)}
    before = next((i for i, b in enumerate(kids)
                   if b["type"].startswith("heading") and norm(text_of(b)) in section_heads),
                  len(kids))
    print(f"{path}\n  page has {len(kids)} top-level block(s)\n")

    print(f"  before the first section: {before} block(s)")
    if before:
        for b in kids[:before]:
            print(f"      {b['type']:20} {text_of(b)[:82]}")
        print(f"      repo counterpart: {len(body)} line(s)"
              f"{' (nothing publishable; all local)' if doc_preamble(path)[1] else ''}")

    orphan = [b for b in kids if b["type"].startswith("heading")
              and norm(text_of(b)) not in repo_heads]
    print(f"\n  headings on the page with no repo counterpart: {len(orphan)}")
    for b in orphan:
        print(f"      {b['type']:20} {text_of(b)[:82]}")


RENDERED = {"heading_1", "heading_2", "heading_3", "heading_4", "paragraph", "bulleted_list_item",
            "numbered_list_item", "to_do", "code", "table", "table_row", "divider",
            "quote", "toggle", "callout", "child_page"}


def walk_all(bid, depth=0, out=None):
    out = out if out is not None else []
    cur = None
    while True:
        q = "?page_size=100" + (f"&start_cursor={cur}" if cur else "")
        j = api(f"/blocks/{bid}/children{q}")
        for b in j["results"]:
            out.append((depth, b))
            if b.get("has_children") and b["type"] not in ("child_page", "child_database"):
                walk_all(b["id"], depth + 1, out)
        if not j.get("has_more"):
            return out
        cur = j["next_cursor"]


def hydrated(blocks):
    """Read a complete tree, without entering another document."""
    out = copy.deepcopy(blocks)
    for b in out:
        if b.get("has_children") and b["type"] not in ("child_page", "child_database"):
            b[b["type"]]["children"] = hydrated(top_children(b["id"]))
    return out


def rich_key(rt, live):
    # API segmentation is arbitrary. Merge adjacent runs with identical formatting.
    runs = []
    for r in rt or []:
        content = r.get("plain_text", "") if live else r["text"]["content"]
        href = r.get("href") if live else (r.get("text", {}).get("link") or {}).get("url")
        ann = r.get("annotations") or {}
        style = (href, *(bool(ann.get(k)) for k in ("bold", "italic", "code", "strikethrough", "underline")),
                 ann.get("color", "default"), r.get("type", "text"))
        if runs and runs[-1][0] == style:
            runs[-1] = (style, runs[-1][1] + content)
        elif content:
            runs.append((style, content))
    return runs


def own_key(b, live):
    """Everything a PATCH to this block would overwrite, and nothing its children hold."""
    kind = b["type"]
    body = b[kind]
    return (kind, rich_key(body.get("rich_text"), live),
            tuple((k, body.get(k, default)) for k, default in
                  (("language", None), ("checked", False), ("is_toggleable", False),
                   ("table_width", None), ("has_column_header", False),
                   ("has_row_header", False), ("color", "default"))),
            body.get("icon"), [rich_key(c, live) for c in body.get("cells", [])])


def block_key(b, live):
    return (own_key(b, live),
            [block_key(c, live) for c in b[b["type"]].get("children", [])])


def same_blocks(live, new):
    return [block_key(b, True) for b in live] == [block_key(b, False) for b in new]


def descendants(blocks):
    for b in blocks:
        yield b
        yield from descendants(b[b["type"]].get("children", []))


def loss_reasons(blocks):
    reasons = set()
    for b in descendants(blocks):
        kind, body = b["type"], b[b["type"]]
        if kind not in RENDERED or kind == "child_page":
            reasons.add(kind)
        if body.get("children") and kind != "table":
            lists = ("bulleted_list_item", "numbered_list_item", "to_do")
            if kind == "toggle":
                # The converter writes a toggle's children, but never a toggle inside a
                # toggle or a heading inside one, so neither can be reproduced.
                if any(c["type"] == "toggle" for c in body["children"]):
                    reasons.add("nested toggle")
                if any(c["type"].startswith("heading") for c in body["children"]):
                    reasons.add("heading inside toggle")
            elif kind.startswith("heading") and body.get("is_toggleable"):
                pass  # the converter nests a toggle heading's section inside it
            elif kind not in lists or any(c["type"] not in lists for c in body["children"]):
                reasons.add("nested content")
        if body.get("color", "default") != "default":
            reasons.add("block colour")
        if body.get("caption"):
            reasons.add("caption")
        if kind == "callout" and (body.get("icon") or {}).get("type") != "emoji":
            reasons.add("non-emoji callout icon")
        rts = [body.get("rich_text", [])] + body.get("cells", [])
        for rt in rts:
            for r in rt:
                a = r.get("annotations") or {}
                if a.get("color", "default") != "default":
                    reasons.add("text colour")
                if r.get("type", "text") != "text":
                    reasons.add("mention/equation")
    return sorted(reasons)


def check_comments(blocks, in_place=False):
    """Refuse to write over a reviewer's thread.

    A deleted block takes its threads with it, so that refusal is absolute. A rewrite keeps
    the block ID, which keeps a thread on the whole block. It does not keep a thread on a
    phrase inside the block: the API's text carries no comment highlights, so the rewrite
    drops the highlight and the thread disappears from the page, unresolved, even when the
    phrase is still in the new text. The API reports both kinds of thread identically, so
    `--allow-comment-edit` names every thread and warns rather than promising either
    outcome. A failed read refuses in every case: an unreadable thread is an unknown one.

    Only open threads reach this check, because the API does not return resolved ones.
    Resolving a thread in Notion is therefore what clears a block for an ordinary write."""
    carried = []
    for b in descendants(blocks):
        res = api(f"/comments?block_id={b['id']}&page_size=100", soft=True)
        if "_error" in res:
            sys.exit(f"REFUSING: cannot check comments on {b['id']}: {res['_error']}. Nothing written.")
        if res.get("results"):
            if in_place and ALLOW_COMMENT_EDIT:
                carried.append((b, res["results"]))
                continue
            fix = (" A rewrite keeps a thread on the whole block and hides a thread on a "
                   "phrase; answer it first, then pass --allow-comment-edit." if in_place else
                   " This write deletes the block, and its thread with it; resolve the "
                   "thread in Notion first, or keep the block in the source.")
            sys.exit(f"REFUSING: block {b['id']} carries comments. Nothing written.{fix}")
    for b, comments in carried:
        threads = sorted({c["discussion_id"] for c in comments})
        print(f"  THREAD UNDER REWRITE: {text_of(b)[:60]!r} carries {len(threads)} thread(s): "
              f"{', '.join(threads)}. A thread on the whole block stays; a thread on a phrase "
              "leaves the page, unresolved.")


def links_in(blocks, live):
    links = set()
    for b in descendants(blocks):
        body = b[b["type"]]
        for rt in [body.get("rich_text", [])] + body.get("cells", []):
            for r in rt:
                href = r.get("href") if live else (r.get("text", {}).get("link") or {}).get("url")
                if href:
                    links.add(href)
    return links


def own_block(b):
    """The block without its children, which is the unit a PATCH rewrites."""
    kind = b["type"]
    return {**b, kind: {k: v for k, v in b[kind].items() if k != "children"}}


def shape(b):
    """What a live block must share with its replacement to be rewritten through its own
    ID. Notion rejects a change to the table width or the toggle setting in an update."""
    kind = b["type"]
    return (kind, b[kind].get("table_width"), bool(b[kind].get("is_toggleable")))


def matched(old, new):
    """The (old, new) positions whose block survives the write, in order.

    Identical blocks are matched first, the way a text diff matches unchanged lines.
    Inside a run that differs, blocks of the same shape are matched in order, so a
    reworded paragraph keeps its ID even when a paragraph is added beside it. Every other old block is deleted and every other new block is created."""
    pairs = []
    exact = difflib.SequenceMatcher(None, [repr(block_key(b, True)) for b in old],
                                    [repr(block_key(b, False)) for b in new], autojunk=False)
    for tag, i1, i2, j1, j2 in exact.get_opcodes():
        if tag == "equal":
            pairs.extend(zip(range(i1, i2), range(j1, j2)))
        elif tag == "replace":
            inner = difflib.SequenceMatcher(None, [shape(b) for b in old[i1:i2]],
                                            [shape(b) for b in new[j1:j2]], autojunk=False)
            for t, a1, a2, b1, b2 in inner.get_opcodes():
                if t == "equal":
                    pairs.extend(zip(range(i1 + a1, i1 + a2), range(j1 + b1, j1 + b2)))
    return pairs


def region_start(container, old, anchor, at_start):
    """Where content placed before the first surviving block goes: after the block that
    precedes the region, or at the start of its container."""
    if not old:
        return anchor, at_start
    ids = [b["id"] for b in top_children(container)]
    at = ids.index(old[0]["id"])
    return (ids[at - 1], False) if at else (None, True)


def edits(container, old, new, anchor=None, at_start=False, plan=None):
    """Every write that turns `old` into `new`, block by block, at every level of nesting.

    A matched block that is identical is not written. A matched block whose own content
    differs is rewritten through its ID, and its children are compared the same way. New
    blocks are created after the block they follow; old blocks with no match are deleted."""
    if plan is None:
        plan = {"kept": 0, "patch": [], "create": [], "delete": [], "nested": []}
    pairs = {j: i for i, j in matched(old, new)}
    survivors = set(pairs.values())
    plan["delete"].extend((container, b) for i, b in enumerate(old) if i not in survivors)
    after, start = region_start(container, old, anchor, at_start)
    run = []
    for j, n in enumerate(new):
        if j not in pairs:
            run.append(n)
            continue
        if run:
            plan["create"].append((container, after, start, run))
            run = []
        o = old[pairs[j]]
        if own_key(o, True) == own_key(n, False):
            plan["kept"] += 1
        else:
            plan["patch"].append((o, n))
        ok, nk = o[o["type"]].get("children", []), n[n["type"]].get("children", [])
        if not same_blocks(ok, nk):
            plan["nested"].append(o)
            edits(o["id"], ok, nk, plan=plan)
        after, start = o["id"], False
    if run:
        plan["create"].append((container, after, start, run))
    return plan


def describe(plan):
    made = sum(len(run) for *_, run in plan["create"])
    return (f"keeps {plan['kept']}, rewrites {len(plan['patch'])} in place, "
            f"creates {made}, deletes {len(plan['delete'])} block(s)")


def guard_edits(plan, check=True):
    """Check what this write deletes or overwrites, and nothing it leaves alone.

    A deleted block takes its comment thread with it, so a thread there refuses outright.
    A rewritten block keeps its ID, which keeps a whole-block thread but not a phrase
    thread, so `--allow-comment-edit` permits it with a warning. A block the write does not touch keeps everything it carries and is not read."""
    doomed = [b for _, b in plan["delete"]]
    olds = [own_block(o) for o, _ in plan["patch"]]
    news = [own_block(n) for _, n in plan["patch"]]
    made = [b for *_, run in plan["create"] for b in run]
    # Deleting an ancestor trashes descendant pages, even with flatten explicitly set.
    if any(b["type"] in ("child_page", "child_database") for b in descendants(doomed)):
        sys.exit("REFUSING: this range contains a nested page or database.")
    # A parent whose children change must itself be a structure the converter writes,
    # or the edit would rearrange content the repository cannot hold, such as a
    # paragraph someone nested under a list item on the page.
    structural = {"nested content", "nested toggle", "heading inside toggle"}
    reasons = sorted(set(loss_reasons(doomed + olds))
                     | (set(loss_reasons(plan["nested"])) & structural))
    if reasons and not ALLOW_FLATTEN:
        sys.exit(f"REFUSING: this write cannot preserve {', '.join(reasons)}.")
    lost = links_in(doomed + olds, True) - links_in(made + news, False)
    if (lost or UNRESOLVED) and not ALLOW_LINK_LOSS:
        sys.exit(f"REFUSING: unresolved or removed links: {sorted(lost | set(UNRESOLVED))}")
    for *_, run in plan["create"]:
        for n in range(0, len(run), 100):
            if len(json.dumps({"children": run[n:n + 100], "after": "x" * 36}).encode()) > 500_000:
                sys.exit("REFUSING: request exceeds 500KB. Publish a smaller subsection.")
    if check:
        check_comments(doomed)
        check_comments(olds, in_place=True)


def validate_payload(blocks):
    """Check request limits before any writes, including nested table rows."""
    def visit(value):
        if isinstance(value, list):
            if len(value) > 100:
                sys.exit("REFUSING: a nested payload array exceeds 100 items; split the section/table.")
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in ("content", "url") and isinstance(item, str) and len(item) > LIMIT:
                    sys.exit(f"REFUSING: {key} exceeds {LIMIT} characters.")
                visit(item)
    def nesting(block):
        kids = block[block["type"]].get("children", [])
        return 1 + max(nesting(k) for k in kids) if kids else 0
    for b in blocks:
        visit(b)
        # Notion accepts two levels of children below a block in one request. A table
        # inside a toggle heading is two; a toggle inside a toggle holding a table is three.
        if nesting(b) > 2:
            sys.exit("REFUSING: content nests more than two levels below a block, and Notion "
                     "accepts two per request. Remove one toggle level, or publish the inner "
                     "section on its own.")


def append_run(container, blocks, anchor, at_start, fresh):
    """Create one run of blocks after `anchor`, checking that each chunk lands there.

    `fresh` holds every block this write has created so far, in any container, so a
    rejected chunk rolls back the whole create phase rather than this run alone."""
    made_here = []
    existing_ids = {b["id"] for b in top_children(container)}
    for n in range(0, len(blocks), 100):
        request = {"children": blocks[n:n + 100]}
        if anchor:
            request["after"] = anchor
        elif at_start and n == 0:
            request["position"] = {"type": "start"}
        if len(json.dumps(request).encode()) > 500_000:
            sys.exit("REFUSING: request exceeds 500KB. Publish a smaller subsection.")
        res = api(f"/blocks/{container}/children", "PATCH", request, soft=True)
        if "_error" in res:
            if res.get("_uncertain"):
                sys.exit(f"WRITE OUTCOME UNKNOWN: {res['_detail']}. "
                         f"Old blocks retained. Known new IDs: {fresh}. Inspect before retrying.")
            failed_rollback = []
            for bid in fresh:
                undo = api(f"/blocks/{bid}", "DELETE", soft=True)
                if "_error" in undo:
                    failed_rollback.append(bid)
            sys.exit(f"Append rejected: {res['_detail']}. "
                     f"Old blocks retained; rollback leftovers: {failed_rollback}.")
        # Some append responses include existing siblings or only the first page.
        # Identify creations from a complete before/after read, never from results
        # alone: rollback must never treat an existing page block as newly created.
        current = top_children(container)
        made = [b["id"] for b in current if b["id"] not in existing_ids]
        if len(made) != len(request["children"]):
            sys.exit(f"WRITE OUTCOME UNKNOWN: created-block count does not match the request. "
                     f"Old blocks retained. New candidate IDs: {fresh + made}.")
        positions = [i for i, b in enumerate(current) if b["id"] in made]
        if anchor:
            expected_start = next((i + 1 for i, b in enumerate(current)
                                   if b["id"] == anchor), None)
        else:
            expected_start = 0 if at_start and n == 0 else len(current) - len(made)
        if expected_start is None or positions != list(range(expected_start, expected_start + len(made))):
            sys.exit("WRITE OUTCOME UNKNOWN: inserted blocks are in the wrong position. "
                     f"Old blocks retained. New candidate IDs: {fresh + made}.")
        fresh.extend(made)
        made_here.extend(made)
        existing_ids.update(made)
        anchor = made[-1]
    return made_here


def apply_edits(plan):
    """Create and read back, rewrite, check the doomed blocks again, then delete.

    Deletion comes last, so a failure at any earlier step leaves every old block on the
    page. Nothing here is a transaction; the messages name what has already happened."""
    fresh = []
    for container, after, start, run in plan["create"]:
        made = append_run(container, run, after, start, fresh)
        live = hydrated([b for b in top_children(container) if b["id"] in set(made)])
        if [b["id"] for b in live] != made or not same_blocks(live, run):
            sys.exit(f"READ-BACK FAILED: old blocks retained. Inspect new IDs: {fresh}.")
    written = []
    for o, n in plan["patch"]:
        kind = o["type"]
        res = api(f"/blocks/{o['id']}", "PATCH", {kind: own_block(n)[kind]}, soft=True)
        if "_error" in res:
            sys.exit(f"PARTIAL UPDATE: created {fresh}; rewrote {written}; {o['id']} failed: "
                     f"{res['_detail']}. No block was deleted. Run verify before retrying.")
        if own_key(res, True) != own_key(n, False):
            sys.exit(f"READ-BACK FAILED: created {fresh}; rewrote {written}; {o['id']} did not "
                     "take the new content. No block was deleted. Run verify before retrying.")
        written.append(o["id"])
    doomed = [b for _, b in plan["delete"]]
    for container in dict.fromkeys(c for c, _ in plan["delete"]):
        ids = {b["id"] for c, b in plan["delete"] if c == container}
        fetched = {b["id"]: b for b in hydrated([b for b in top_children(container) if b["id"] in ids])}
        if any(b["id"] not in fetched or block_key(fetched[b["id"]], True) != block_key(b, True)
               for c, b in plan["delete"] if c == container):
            sys.exit(f"CONCURRENT EDIT: original content changed during sync; nothing deleted. "
                     f"Inspect new IDs: {fresh}.")
    deleted = []
    for b in doomed:
        res = api(f"/blocks/{b['id']}", "DELETE", soft=True)
        if "_error" in res:
            sys.exit(f"PARTIAL DELETE: new content is present; deleted {deleted}; "
                     f"remaining old IDs: {[x['id'] for x in doomed if x['id'] not in deleted]}. "
                     "Run verify and inspect before retrying.")
        deleted.append(b["id"])
    print(f"  kept {plan['kept']}, rewrote {len(written)} in place, created {len(fresh)}, "
          f"deleted {len(deleted)} block(s). Run verify for the whole document.")


def replace_blocks(container, old, new, anchor=None, at_start=False):
    """Every mutation route: match the blocks, guard what changes, then write it."""
    validate_payload(new)
    plan = edits(container, old, new, anchor, at_start)
    guard_edits(plan)
    apply_edits(plan)


def page_preamble(path, kids):
    _, masked = body_of(path)
    depth = section_depth(masked)
    title = next((strip_mark(l.lstrip("# ")) for l in masked if re.match(r"^#{1,4} ", l)), "")
    start = 1 if kids and kids[0]["type"] == "heading_1" and norm(text_of(kids[0])) == norm(title) else 0
    end = next((i for i in range(start, len(kids))
                if kids[i]["type"].startswith("heading")
                and int(kids[i]["type"].split("_")[1]) <= depth), len(kids))
    return [b for b in kids[start:end] if b["type"] not in ("child_page", "child_database")]


def page_heading_entries(kids, parent):
    entries = []
    def search(siblings, container, inherited):
        stack = []
        for i, b in enumerate(siblings):
            scope = inherited + [title for _, title in stack]
            if b["type"].startswith("heading"):
                depth = int(b["type"].split("_")[1])
                while stack and stack[-1][0] >= depth:
                    stack.pop()
                stack.append((depth, text_of(b)))
                scope = inherited + [title for _, title in stack]
                entries.append((siblings, i, container, " > ".join(scope)))
            if b.get("has_children") and b["type"] not in ("child_page", "child_database", "table"):
                search(top_children(b["id"]), b["id"], scope)
    search(kids, parent, [])
    return entries


def locate(kids, heading, parent, by_number=False):
    """Resolve a heading inside its parent scope, refusing ambiguous bare names."""
    entries = page_heading_entries(kids, parent)
    matches = [(siblings, i, container) for siblings, i, container, scope in entries
               if selector_matches(scope, heading)]
    if not matches and by_number:
        leaf = strip_mark(leaf_heading(heading).lstrip("# "))
        num = re.match(r"^(\d+)\.\s", leaf)
        parents = heading.rsplit(" > ", 1)[0] if " > " in heading else None
        if num:
            matches = [(siblings, i, container) for siblings, i, container, scope in entries
                       if re.match(rf"^{num.group(1)}\.\s", text_of(siblings[i]))
                       and (parents is None or (" > " in scope and selector_matches(scope.rsplit(" > ", 1)[0], parents)))]
    if len(matches) > 1:
        sys.exit(f"REFUSING: ambiguous page heading {heading!r}; qualify it with its parent.")
    return matches[0] if matches else None


def heading_payload(line):
    marks, title = leaf_heading(line).split(" ", 1)
    return para(f"heading_{len(marks)}", strip_mark(title))


def heading_matches(block, line):
    line = leaf_heading(line)
    want = heading_payload(line)
    kind = want["type"]
    return (block["type"] == kind
            and rich_key(block[kind].get("rich_text"), True) == rich_key(want[kind]["rich_text"], False)
            and bool(block[kind].get("is_toggleable")) == (TOGGLE_MARK in line))


def intro_lines(body):
    masked = mask_fences(body)
    cut = next((i for i, l in enumerate(masked) if re.match(r"^#{1,4} ", l)), len(body))
    return body[:cut]


def intro_blocks(kids, idx):
    blocks = section_blocks(kids, idx)
    end = next((i for i, b in enumerate(blocks) if b["type"].startswith("heading")), len(blocks))
    return blocks[:end]


def repo_regions(path):
    raw, _, entries = repo_heading_entries(path)
    regions = []
    for n, (i, selector) in enumerate(entries):
        end = entries[n + 1][0] if n + 1 < len(entries) else len(raw)
        regions.append((selector, raw[i + 1:end], MANUAL_MARK in selector))
    return regions


def me_id():
    """The account this token acts as, used to tell my own replies from a reviewer's."""
    return (json.loads(Path(TOKEN).read_text()).get("owner") or {}).get("user", {}).get("id")


def page_threads(pid, kids):
    """Every comment on the page, in page order, with the heading region it sits under.

    Notion has no endpoint for the comments of a page, only for those of one block, so
    this asks every block and every descendant in turn. A long page is therefore a long
    scan. There is no shortcut: nothing in a block says whether it carries a thread.

    The endpoint returns open threads only. A resolved one is not returned at all, which
    is why this reads as a work list rather than a history, and why a block stops
    refusing writes once its thread is resolved.

    One comment can answer to more than one block: a thread on a table comes back for
    the table and again for each of its rows. The first block to report a comment keeps
    it, which is the outermost one, so a table thread is reported against the table
    rather than once per row."""
    found, seen = [], set()

    def collect(block_id, heading, anchor):
        res = api(f"/comments?block_id={block_id}&page_size=100", soft=True)
        if "_error" in res:
            sys.exit(f"REFUSING: cannot read comments on {block_id}: {res['_error']}. "
                     "A thread that cannot be read is an unknown one, not an absent one.")
        for c in res.get("results", []):
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            found.append((heading, anchor, c))

    collect(pid, "@page", None)
    heading = "@preamble"
    for top in hydrated(kids):
        if top["type"].startswith("heading"):
            heading = strip_mark(text_of(top))
        for b in descendants([top]):
            if b["type"] in ("child_page", "child_database"):
                continue
            collect(b["id"], heading, b)
    return found


def thread_list(pid, kids):
    """The page's comments grouped into threads, each in the order they were written."""
    threads, order = {}, []
    for heading, anchor, c in page_threads(pid, kids):
        d = c["discussion_id"]
        if d not in threads:
            threads[d] = {"heading": heading, "anchor": anchor, "comments": []}
            order.append(d)
        threads[d]["comments"].append(c)
    for d in order:
        threads[d]["comments"].sort(key=lambda c: c.get("created_time") or "")
    return [(d, threads[d]) for d in order]


def show_thread(d, t, mine, indent="  "):
    anchor = t["anchor"]
    where = f"{anchor['type']} {text_of(anchor)[:60]!r}" if anchor else "the page itself"
    last = t["comments"][-1]
    waiting = last["created_by"]["id"] != mine
    print(f"{indent}{t['heading']}")
    print(f"{indent}  on {where}")
    if any(c.get("original_content_deleted") for c in t["comments"]):
        print(f"{indent}  the text this thread was anchored to has been deleted")
    print(f"{indent}  discussion {d}{'  [waiting on you]' if waiting else ''}")
    for c in t["comments"]:
        who = (c.get("display_name") or {}).get("resolved_name") or c["created_by"]["id"]
        when = (c.get("created_time") or "")[:10]
        body = "".join(r.get("plain_text", "") for r in c.get("rich_text", []))
        body = body.strip().replace("\n", f"\n{indent}        ")
        print(f"{indent}    {when} {who}: {body}")
    return waiting


@read_snapshot
def cmd_comments(path):
    """Every thread on the page, as the work list a review leaves behind."""
    pid = page_id(path)
    found = thread_list(pid, top_children(pid))
    print(path)
    waiting = 0
    for d, t in found:
        waiting += show_thread(d, t, me_id())
    print(f"  {len(found)} open thread(s), {waiting} waiting on you.")
    print("  Resolved threads are not returned by the API and cannot be resolved through it. "
          "Resolve in Notion, and the thread leaves this list.")
    return 0


@read_snapshot
def cmd_reply(path, discussion, text, confirmed):
    """Answer one thread, after proving it belongs to this document's page.

    The id is checked against the page rather than trusted, because a discussion id from
    another document is still a valid id: posting to the wrong one puts words under
    someone else's work, where the only repair is a deletion they will see."""
    pid = page_id(path)
    found = dict(thread_list(pid, top_children(pid)))
    if discussion not in found:
        sys.exit(f"REFUSING: no thread {discussion!r} on this document's page. "
                 f"Run `comments {path}` for the threads that are there.")
    print(path)
    show_thread(discussion, found[discussion], me_id())
    print("  reply:")
    for line in text.split("\n"):
        print(f"      {line}")
    if not confirmed:
        print("Nothing sent. Re-run with --yes to post it.")
        return 0
    res = api("/comments", "POST", {"discussion_id": discussion, "rich_text": rich(text)}, soft=True)
    if "_error" in res:
        sys.exit(f"reply failed: {res['_error']} {res.get('_detail')}. Nothing was posted "
                 "unless the response was lost; run `comments` before retrying.")
    print("  posted. Resolve the thread in Notion when it is settled; the API cannot.")
    return 0


@read_snapshot
def cmd_verify(path):
    """Compare the preamble and every heading/intro, including nested sections."""
    global BASE_DIR
    BASE_DIR = os.path.dirname(os.path.abspath(path))
    pid = page_id(path)
    kids = top_children(pid)
    failures, owned, claimed = [], [], set()
    pre = page_preamble(path, kids)
    pre_lines, _ = doc_preamble(path)
    if not same_blocks(hydrated(pre), convert(pre_lines)):
        failures.append("@preamble")
    claimed.update(b["id"] for b in descendants(hydrated(pre)))
    regions = repo_regions(path)
    for h, body, manual in regions:
        hit = locate(kids, h, pid)
        if not hit:
            (owned if manual else failures).append(f"{strip_mark(h)}: missing heading")
            continue
        siblings, idx, _ = hit
        head = siblings[idx]
        live = hydrated(intro_blocks(siblings, idx))
        claimed.add(head["id"])
        claimed.update(b["id"] for b in descendants(live))
        try:
            matches = same_blocks(live, convert(body)) and heading_matches(head, h)
            reasons = loss_reasons(live)
        except SystemExit as e:
            matches, reasons = False, [str(e)]
        if not matches or reasons:
            (owned if manual else failures).append(strip_mark(h) + (f" ({', '.join(reasons)})" if reasons else ""))
    # Ignore a matching body title and explicitly separate other documents.
    _, masked = body_of(path)
    title = next((strip_mark(l.lstrip("# ")) for l in masked if re.match(r"^#{1,4} ", l)), "")
    if kids and kids[0]["type"] == "heading_1" and text_of(kids[0]) == title:
        claimed.add(kids[0]["id"])
    all_blocks = list(descendants(hydrated(kids)))
    wanted_order = [h for h, _, manual in regions if not manual]
    actual_order = []
    for _, _, _, scope in page_heading_entries(kids, pid):
        matches = [h for h in wanted_order if selector_matches(scope, h)]
        if len(matches) == 1:
            actual_order.append(matches[0])
    if actual_order != wanted_order:
        failures.append("managed heading order/count differs")
    preserved = [b for b in all_blocks if b["type"] in ("child_page", "child_database")]
    extra = [b for b in all_blocks if b["id"] not in claimed and b not in preserved]
    if extra:
        # One block per line. A single long list is easy to scroll past or filter away,
        # and an unclaimed block is how a duplicated or orphaned section shows up.
        lines = [f"unclaimed page blocks: {len(extra)}"]
        for b in extra[:UNCLAIMED_SHOWN]:
            lines.append(f"      {b['type']:<20} {b['id']}  {text_of(b)[:60]!r}")
        if len(extra) > UNCLAIMED_SHOWN:
            lines.append(f"      ... and {len(extra) - UNCLAIMED_SHOWN} more")
        failures.append("\n  FAIL ".join(lines))
    if UNRESOLVED:
        failures.append(f"unresolved links: {sorted(set(UNRESOLVED))}")
    print(path)
    for f in failures:
        print(f"  FAIL {f}")
    for f in owned:
        print(f"  MANUAL (page-owned) {f}")
    print(f"  preserved nested pages/databases: {len(preserved)}")
    print(f"  {'FAIL' if failures else 'PASS'} managed body: preamble and {len(regions)} heading regions")
    if WITH_COMMENTS:
        # Reported beside the result, not inside it: a matching body with open threads is
        # in sync and still has review work outstanding.
        threads, mine = thread_list(pid, kids), me_id()
        waiting = [(d, t) for d, t in threads if t["comments"][-1]["created_by"]["id"] != mine]
        print(f"  open threads: {len(threads)}, {len(waiting)} waiting on you")
        for d, t in waiting:
            first = "".join(r.get("plain_text", "") for r in t["comments"][0].get("rich_text", []))
            print(f"    {t['heading']}: {first.strip()[:60]!r}  discussion {d}")
        print("  Not checked: page title property, reviewer properties, concurrent edits.")
    else:
        print("  Not checked: page title property, reviewer properties, comments "
              "(add --with-comments), concurrent edits.")
    return 1 if failures else 0


@read_snapshot
def cmd_manual(path):
    """Report each independently addressable region without hiding parent intros."""
    global BASE_DIR
    BASE_DIR = os.path.dirname(os.path.abspath(path))
    pid = page_id(path)
    kids = top_children(pid)
    print(path)
    regions = [("@preamble", doc_preamble(path)[0], False)] + repo_regions(path)
    for h, body, manual in regions:
        UNRESOLVED.clear()
        if h == "@preamble":
            live = hydrated(page_preamble(path, kids))
            head_ok = True
        else:
            hit = locate(kids, h, pid)
            if not hit:
                print(f"  {'MANUAL' if manual else 'MISSING'} {strip_mark(h)}")
                continue
            siblings, idx, _ = hit
            live = hydrated(intro_blocks(siblings, idx))
            head_ok = heading_matches(siblings[idx], h)
        try:
            new = convert(body)
            same = same_blocks(live, new) and head_ok
            reasons = loss_reasons(live)
            if UNRESOLVED:
                reasons.append("unresolved links")
        except SystemExit as e:
            same, reasons = False, [str(e)]
        if same and not reasons:
            state = "IN SYNC"
        elif manual:
            state = "MANUAL: page-owned"
        elif reasons:
            state = "BLOCKED: " + ", ".join(reasons)
        else:
            state = "DIFFERS: plan/apply this region" + (" with --preamble" if h != "@preamble" else "")
        print(f"  {state}: {strip_mark(h)}")
    print("  Read-only classification; apply also checks comments and removed links.")


def assert_writable_region(path, heading):
    for h, _, manual in repo_regions(path):
        if selector_matches(h, heading) and manual:
            sys.exit(f"REFUSING: {strip_mark(h)} belongs to a manual section.")


def update_heading(live, line):
    kind = live["type"]
    want = heading_payload(line)
    if want["type"] != kind:
        sys.exit("REFUSING: heading depth differs; repair the heading type on the page first.")
    desired_toggle = TOGGLE_MARK in leaf_heading(line)
    if desired_toggle != bool(live[kind].get("is_toggleable")):
        sys.exit("REFUSING: toggle layout differs; repair the container on the page before syncing.")
    if not heading_matches(live, line):
        check_comments([live], in_place=True)
        api(f"/blocks/{live['id']}", "PATCH", {kind: want[kind]})
        print("  updated heading; body block IDs preserved.")


def main():
    global PREAMBLE, ALLOW_FLATTEN, ALLOW_LINK_LOSS, ALLOW_COMMENT_EDIT, WITH_COMMENTS, IMPORTED, BASE_DIR
    PREAMBLE = "--preamble" in sys.argv
    ALLOW_FLATTEN = "--allow-flatten" in sys.argv
    ALLOW_LINK_LOSS = "--allow-link-loss" in sys.argv
    ALLOW_COMMENT_EDIT = "--allow-comment-edit" in sys.argv
    WITH_COMMENTS = "--with-comments" in sys.argv
    UNRESOLVED.clear()
    argv = [a for a in sys.argv[1:] if a not in
            ("--yes", "--preamble", "--allow-flatten", "--allow-link-loss",
             "--allow-comment-edit", "--with-comments")]
    if len(argv) < 2:
        sys.exit(__doc__)
    mode, path = argv[:2]
    BASE_DIR = os.path.dirname(os.path.abspath(path))
    source = Path(path).read_text()
    IMPORTED = bool(re.search(r"^imported:[ \t]*true\s*$", source.split("---", 2)[1], re.M))
    confirmed = "--yes" in sys.argv
    if mode == "verify" and len(argv) == 2:
        sys.exit(cmd_verify(path))
    if mode == "manual" and len(argv) == 2:
        return cmd_manual(path)
    if mode == "coverage" and len(argv) == 2:
        return cmd_coverage(path)
    if mode == "comments" and len(argv) == 2:
        return cmd_comments(path)
    if mode == "reply" and len(argv) == 4:
        return cmd_reply(path, argv[2], argv[3], confirmed)
    if mode == "drop" and len(argv) == 3:
        return cmd_drop(path, argv[2], confirmed)
    if mode == "insert" and len(argv) == 4:
        return cmd_insert(path, argv[2], argv[3], confirmed)
    if mode not in ("plan", "apply") or len(argv) != 3:
        sys.exit(__doc__)
    heading = argv[2]
    pid = page_id(path)
    kids = top_children(pid)
    live_head = head_line = None
    at_start = False
    if heading == "@preamble":
        body, _ = doc_preamble(path)
        doomed = page_preamble(path, kids)
        container = pid
        anchor = doomed[0]["id"] if doomed else None
        if not doomed:
            _, masked = body_of(path)
            title = next((strip_mark(l.lstrip("# ")) for l in masked if re.match(r"^#{1,4} ", l)), "")
            if kids and kids[0]["type"] == "heading_1" and norm(text_of(kids[0])) == norm(title):
                anchor = kids[0]["id"]
            else:
                at_start = True
    else:
        head_line, body = repo_section(path, heading)
        assert_writable_region(path, heading)
        if PREAMBLE:
            body = intro_lines(body)
        hit = locate(kids, heading, pid, by_number=True)
        if not hit:
            sys.exit(f"no heading on the page matching {head_line!r}")
        siblings, idx, container = hit
        live_head = siblings[idx]
        want = heading_payload(head_line)
        if want["type"] != live_head["type"]:
            sys.exit("REFUSING: heading depth differs; repair the heading type on the page first.")
        toggle = bool(live_head[live_head["type"]].get("is_toggleable"))
        if toggle != (TOGGLE_MARK in head_line):
            sys.exit("REFUSING: toggle layout differs; repair the container before syncing.")
        if toggle:
            assert_toggle_section_clean(siblings, idx)
            container = live_head["id"]
            inner = top_children(container)
            end = next((i for i, b in enumerate(inner) if b["type"].startswith("heading")), len(inner)) if PREAMBLE else len(inner)
            doomed = inner[:end]
            anchor = doomed[0]["id"] if doomed else None
            if PREAMBLE and inner and not doomed and any(l.strip() for l in body):
                sys.exit("REFUSING: no intro block inside toggle to anchor after.")
        else:
            doomed = intro_blocks(siblings, idx) if PREAMBLE else section_blocks(siblings, idx)
            anchor = live_head["id"]
        doomed = [b for b in doomed if b["type"] not in ("child_page", "child_database")]
    old = hydrated(doomed)
    new = convert(body)
    validate_payload(new)
    differs = not same_blocks(old, new)
    head_differs = live_head is not None and not heading_matches(live_head, head_line)
    text_differs = show_diff(old, new, top_children)
    if differs and not text_differs:
        print("  block structure/settings differ; inspect block types, table settings, or formatting")
    if head_differs:
        print(f"  heading differs: {text_of(live_head)!r} -> {strip_mark(head_line)!r}")
    if mode == "plan":
        try:
            plan = edits(container, old, new, anchor, at_start)
            if differs:
                print(f"  write {describe(plan)}")
            guard_edits(plan, check=False)
        except SystemExit as e:
            print(f"  BLOCKED: {e}")
        print("plan only, nothing written. Comment checks run on apply.")
        return
    if not differs and not head_differs:
        print("Nothing to do. Existing block IDs and comments preserved.")
        return
    if head_differs:
        # Preflight before any body mutation.
        check_comments([live_head], in_place=True)
    if differs:
        replace_blocks(container, old, new, anchor, at_start=at_start)
    if head_differs:
        update_heading(live_head, head_line)


if __name__ == "__main__":
    main()
