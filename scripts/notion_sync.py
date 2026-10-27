#!/usr/bin/env python3
"""Compare code blocks on a Notion page against the repo, and repair whitespace damage.

    python3 scripts/notion_sync.py check <doc.md>
    python3 scripts/notion_sync.py apply <doc.md>

Pasting into Notion has lost whitespace inside code blocks, producing non-compiling
samples on published pages. This repairs that and nothing else.

A Notion block is paired with a repo fence only when the two are identical once all
whitespace is removed. If any other character differs the block is reported and left
alone: the repo is not authoritative for prose, and a silent overwrite is how an
author's wording disappears.

Page properties are never written. Reviewers own them, and the boundary is
enforced here rather than remembered.
"""

import json, os, re, sys, urllib.error, urllib.parse, urllib.request
from notion_publish import body_of, fence_open, fence_close

TOKEN_PATH = os.path.expanduser("~/.config/notion-sync/notion_token.json")
NOTION_VERSION = "2022-06-28"
RICH_TEXT_LIMIT = 2000


def token():
    with open(TOKEN_PATH) as f:
        return json.load(f)["access_token"]


def api(path, method="GET", body=None):
    req = urllib.request.Request(
        f"https://api.notion.com/v1{path}",
        data=json.dumps(body).encode() if body else None,
        method=method,
        headers={"Authorization": f"Bearer {token()}",
                 "Notion-Version": NOTION_VERSION,
                 "Content-Type": "application/json",
                 "User-Agent": "notion-sync/1.0"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} failed ({e.code}): {e.read().decode()[:400]}")


def walk(block_id, depth=0):
    """Every block on the page, including children of toggles and headings."""
    out, cursor = [], None
    while True:
        q = "?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        j = api(f"/blocks/{block_id}/children{q}")
        for b in j["results"]:
            out.append(b)
            # A nested page is its own document with its own repo file. Descending into
            # one would compare its blocks against this document's fences: 004 is a child
            # of 003, and without this the two get mixed.
            if b.get("has_children") and b["type"] not in ("child_page", "child_database"):
                out.extend(walk(b["id"], depth + 1))
        if not j.get("has_more"):
            return out
        cursor = j["next_cursor"]


def plain(block):
    return "".join(t["plain_text"] for t in block[block["type"]].get("rich_text", []))


def repo_fences(path):
    lines, _ = body_of(path)
    fences, body, fence = [], [], None
    for line in lines:
        if fence:
            if fence_close(line, fence):
                fences.append("\n".join(body))
                body, fence = [], None
            else:
                body.append(line)
        elif fence_open(line):
            fence = fence_open(line)
    return fences


def published_url(path):
    m = re.search(r"^published_url:[ \t]*(\S+)$", open(path).read(), re.M)
    if not m:
        sys.exit(f"{path} has no published_url in frontmatter.")
    return m.group(1)


def page_id(url):
    """The id is the last 32 hex characters of the path, after the readable slug."""
    path = urllib.parse.urlparse(url).path
    cleaned = re.sub(r"[^0-9a-zA-Z]", "", path)[-32:]
    if not re.fullmatch(r"[0-9a-f]{32}", cleaned):
        sys.exit(f"No page id found in {url}")
    return cleaned


def squash(s):
    return re.sub(r"\s+", "", s)


def list_pages():
    """Everything the token can reach, which is what was granted at consent."""
    results, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        j = api("/search", "POST", body)
        results.extend(j["results"])
        if not j.get("has_more"):
            break
        cursor = j["next_cursor"]

    print(f"{len(results)} object(s) reachable:\n")
    for o in results:
        kind = o["object"]
        title = ""
        if kind == "page":
            for prop in o.get("properties", {}).values():
                if prop.get("type") == "title":
                    title = "".join(t["plain_text"] for t in prop["title"])
                    break
        elif kind == "database":
            title = "".join(t["plain_text"] for t in o.get("title", []))
        print(f"  {kind:9} {o['id']}  {title[:70]}")


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "pages":
        return list_pages()
    if len(sys.argv) not in (3, 4) or sys.argv[1] not in ("check", "apply"):
        sys.exit(__doc__)
    mode, path = sys.argv[1], sys.argv[2]
    only = set(sys.argv[3].split(",")) if len(sys.argv) > 3 else None

    fences = repo_fences(path)
    by_squash = {}
    for f in fences:
        by_squash.setdefault(squash(f.rstrip("\n")), []).append(f.rstrip("\n"))

    pid = page_id(published_url(path))
    blocks = [b for b in walk(pid) if b["type"] == "code"]
    print(f"{path}\n  page {pid}: {len(blocks)} code blocks, repo has {len(fences)} fences\n")

    repairs, clean, unmatched = [], 0, []
    for b in blocks:
        live = plain(b)
        key = squash(live)
        cands = by_squash.get(key, [])
        if not cands:
            unmatched.append(live.splitlines()[0][:70] if live else "(empty)")
            continue
        if len(set(cands)) > 1:
            unmatched.append(f"ambiguous: {live.splitlines()[0][:60]}")
            continue
        want = cands[0]
        if want == live:
            clean += 1
        else:
            repairs.append((b["id"], live, want))

    print(f"  identical      : {clean}")
    print(f"  whitespace-off : {len(repairs)}")
    print(f"  no repo match  : {len(unmatched)}   (left alone)")
    for u in unmatched:
        print(f"      - {u}")

    for bid, live, want in repairs:
        print(f"\n  --- block {bid}")
        lw, ww = live.splitlines(), want.splitlines()
        for i in range(max(len(lw), len(ww))):
            a = lw[i] if i < len(lw) else ""
            c = ww[i] if i < len(ww) else ""
            if a != c:
                print(f"      notion: {a}")
                print(f"      repo  : {c}")
        if len(want) > RICH_TEXT_LIMIT:
            sys.exit(f"      REFUSING: {len(want)} chars exceeds the {RICH_TEXT_LIMIT} rich-text cap.")

    if only is not None:
        repairs = [r for r in repairs if r[0] in only]
        print(f"\n  restricted to {len(repairs)} named block(s).")

    if mode == "check":
        print(f"\ncheck only, nothing written. {len(repairs)} block(s) would change.")
        return

    for bid, _, want in repairs:
        api(f"/blocks/{bid}", "PATCH",
            {"code": {"rich_text": [{"type": "text", "text": {"content": want}}]}})
        print(f"  patched {bid}")
    print(f"\n{len(repairs)} block(s) written.")


if __name__ == "__main__":
    main()
