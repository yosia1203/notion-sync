---
name: writing-for-notion
description: Use when writing or revising a markdown document that will be published to a Notion page, when a published page has drifted from the repository, or when deciding what markdown a document may contain. Covers the three routes into Notion, the supported markdown, and the conventions that stand in for Notion blocks. To run the sync itself, use the notion-sync skill.
---

# Writing for Notion

Write RFCs, TSDs, proposals, and squad documents for Notion's block model. Before updating an existing page, read [references/publishing.md](references/publishing.md). That file owns the command sequence, authority rules, write guards, and recovery procedure. This skill owns the document conventions.

Files under `performance/` and `knowledge/` are read here and are outside this publishing workflow.

Notion is the delivery destination; the reader's task determines tone and detail. Keep any saved audience brief inside a local block.

## Choose the route

| Route | Use | Boundary |
|---|---|---|
| Import | create a document's first Notion page | each import creates a new page; record its URL in `published_url` |
| `scripts/notion_publish.py` | maintain an existing page | replaces named regions through the API; finish with whole-document verification |
| Paste | manual fallback | tables and fence languages have been lost; editor auto-indent can alter code |

Import and paste do not run the script's local-content filter or link resolver. Prepare a copy with frontmatter, local blocks, and Obsidian comments removed before either route. Resolve repository links to published destinations. Never paste the source file wholesale.

A document without a `published_url` cannot be maintained by the script. A linked target that carries the field and leaves it empty is also a sync blocker, even when its label is visible on the page: the target is waiting for a page, and pointing at it now publishes a dead reference.

## Write supported content

| Source | Script behaviour |
|---|---|
| `#`, `##`, `###`, `####` | heading blocks |
| `#####` and deeper | refuses authored prose; imported prose retains literal syntax with a warning |
| paragraphs, bold, italic, inline code, strikethrough, links | rich text; supported annotations and destinations compared in place |
| `==text==` | underline; Obsidian previews it as a highlight, the page renders it underlined |
| bullets, numbered lists, checkboxes | list/to-do blocks; nested list items retain their parent |
| fenced code | one code block, with long content split into rich-text segments |
| pipe tables | tables with a header row; empty cells and escaped pipes preserved |
| quote starting with an emoji | callout with that emoji as icon |
| `<!-- toggle-block: title -->` ... `<!-- toggle-block:end -->` | a plain toggle block titled with the inline text; the lines between become its children. Refuses a toggle inside a toggle, a heading inside one, and an unclosed marker |
| other blockquote | quote block |
| `---` within the body | divider |
| indented code fence | refuses authored prose; imported prose keeps the literal backticks with a warning. The converter reads a fence only at the start of a line |
| image syntax | refuses; images need a manual section or a separate upload route |
| footnotes and supported HTML tripwires | refuses authored prose; imported prose retains literal syntax with a warning |

The converter is a repository-specific Markdown subset. Matching its output does not prove arbitrary Markdown semantics. Do not assume an untested extension works. Unsupported page content must stay manual rather than being removed to obtain a green result.

Code languages map explicitly: `yaml`, `json`, `go`, `bash`, `shell`, `text`, `plain text`, `mermaid`, `makefile`, `diff`, `markdown`, and `javascript`. An untagged fence means plain text. Unknown tags refuse instead of silently losing highlighting. Longer backtick fences can contain shorter fence examples; every fence must close. Start every fence in column one. The converter cannot nest a code block inside a list item, and an indented fence reaches the page as literal backticks, so it refuses one in authored prose and warns on imported prose. Unindent the fence, or give the section to the page as manual content.

Notion limits each rich-text content segment to 2,000 characters and nested arrays to 100 items. Oversized tables or requests need smaller regions. Table cells hold rich text, not lists, embedded fences, or nested blocks.

## Shape the page for its readers

Choose block types while outlining. Add one when it makes the page easier to scan or lets readers defer supporting detail.

- **Callout:** start a blockquote with an emoji to surface a key decision or warning. Keep ordinary context in a paragraph.
- **Toggle heading:** add `<!-- toggle -->` to a heading when readers can skip its whole section on a first pass. Keep the heading specific enough to describe what opens.
- **Plain toggle:** use `<!-- toggle-block: title -->` and `<!-- toggle-block:end -->` for optional examples or reference details that do not need a section in the page outline.
- Keep the proposal's core and every required action readable with every toggle closed.

Only `scripts/notion_publish.py` recognizes these markers. Import and paste use separate paths, so check their resulting block types or create the callout or toggle in Notion by hand. Blocks absent from the support table, such as images or embeds, need a manual Notion step. Add them after a new-page import; before later script syncs, mark their heading `<!-- manual -->` and describe the intended blocks in a local block. The script reports that region and leaves its content alone. Do not imitate unsupported blocks with HTML or Markdown in a managed section.

## Use the document's heading structure

Use a body title as the first heading. After it, top-level sections use `##` in RFC, proposal and squad documents and `#` in TSDs, as their templates require. Imported wording and structure remain unchanged. The script reads the section depth from the second heading. The preamble is the prose after the title and before the first section.

Do not treat every H1 as a separate page title or every H2 as a document-level section. Verification covers each heading's introduction, including parents with subsections. Repeated names are addressed by their parent path, such as `"## Parent > ### Details"`.

Keep the filing number out of the document title. A page title property and the first body heading are separate fields. The current script updates body headings but does not check or update the page title property, so a rename follows the [title procedure](references/publishing.md#rename-a-page-by-hand) by hand.

## Declare content ownership

| Marker | Meaning |
|---|---|
| `<!-- local:start -->` through `<!-- local:end -->` | repository-only content, removed before every script comparison/write |
| `%%comment%%` | repository-only Obsidian comment, also removed |
| `## Heading <!-- manual -->` | page-owned section and descendants; reported for drift, never written by apply/insert |
| `## Heading <!-- toggle -->` | a toggle heading with its section nested inside, up to the next heading at the same or a higher level; written by the script, including when nested inside another section. Content more than two levels deep refuses |
| `<!-- toggle-block: title -->` ... `<!-- toggle-block:end -->` | a plain toggle block, not a heading; written and compared by the script like any other content |

Local blocks work above the first section, inside a section, or around an entire section. Close them. An unbalanced marker refuses instead of silently excluding the rest of the document. Closed Obsidian comments need no local wrapper; spans, whole lines, and multi-line comments are supported. Examples inside code fences remain literal code.

Review instructions can arrive as Obsidian comments. Check them with `rg -n '%%' documentation/` when working on a document. Answer in conversation and remove a comment only when handled.

Review also arrives as comments on the page, which are a separate set. `comments` lists those threads as a work list and `reply` answers one; the [publishing reference](references/publishing.md#read-and-answer-the-pages-comments) owns both, along with what publishing under a thread does to it. Resolving a thread stays a hand action in Notion.

A manual heading may also be a toggle. Publish an existing toggle by naming it; do not publish its parent if that would recreate the nested toggle as flat content. Changing toggle layout requires a deliberate page repair before applying content.

Imported prose remains its authors' wording. The imported converter exemption is permission to represent that wording, not permission to rewrite it. Record rendering defects in local Import notes.

## Keep links and diagrams usable

Write cross-document links as relative repository paths whose targets carry a real `published_url`. The script resolves them for the page. An in-page Markdown anchor needs a Notion destination; do not rely on a repository heading slug working there.

A link into `knowledge/` or `performance/` is different. Those domains are outside this workflow and their frontmatter has no `published_url` field at all, so no page will ever exist to point at. The script flattens such a link to plain text and does not report it, which keeps the repository path working for a reader here without failing verification forever. Name the finding in the sentence, so the page still says what the reader is being sent to.

Every sequence, state machine, decision path, or pipeline belongs in a fenced `mermaid` block. ASCII flows stay code. Quote punctuation in diagram labels, choose one direction, avoid hard-coded styling, and split a diagram when it becomes difficult to read in a page column.

Do not hard-wrap prose at a visual column width. Keep each prose paragraph on one source line while retaining Markdown structure. Keep headings within four levels. Use Heading 4 for a distinct topic under an H3 when it needs several paragraphs, a diagram, or an example. Use a bold lead-in for a point that does not need another heading.

## Finish the publication

Before publishing, check heading depth, table cells, fence languages, resolvable links, local markers, and manual/toggle ownership. Review whether each callout or toggle helps the reader and whether the main path works with toggles closed. Review the prose for the reader before publishing. Publication checks establish that the content can be carried and compared; editorial review establishes that it serves the reader.

Follow [the publishing procedure](references/publishing.md), ending with `verify`, then close the sync with its [manual check](references/publishing.md#close-every-sync-with-a-manual-check): list the page-owned sections, say what changed in them, and ask the user whether they have updated them. A successful apply covers only its selected region. Report manual content, missing destinations, page-title checks, and any unsupported constructs separately. A failed whole-document check means the sync remains incomplete.

## Sources

- [Import into Notion](https://www.notion.com/help/import-data-into-notion)
- [Headings and dividers](https://www.notion.com/help/columns-headings-and-dividers)
- [Code blocks and Mermaid](https://www.notion.com/help/code-blocks)
- [Heading block types](https://developers.notion.com/reference/block#headings)
- [API request limits](https://developers.notion.com/reference/request-limits)

Script behaviour is defined by `scripts/notion_publish.py` and its regression tests; import and paste are separate paths.
