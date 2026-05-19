---
name: design-doc
description: SimSci Engineering team convention for drafting a Design Document on the IHME hub (Confluence). Use whenever the user asks to "draft a design doc", "write a design document", "make a design doc", "start a PRD", or anything similar where a hub design doc is the deliverable.
---

# SimSci Engineering design documents

Design documents live in the SSE Confluence space under the **Design documents** parent page. There is one canonical template — `_TEMPLATE` — and every doc descends from it. Do not invent a structure; pull the template and follow it.

- Template page id: `210206856` (https://hub.ihme.washington.edu/spaces/SSE/pages/210206856/_TEMPLATE)
- Design documents parent page id: `176589711` (https://hub.ihme.washington.edu/spaces/SSE/pages/176589711/Design+documents)
- Space key: `SSE`

## 1. Read the template

At the start of each invocation, fetch the template body so the section order, header table, and placeholder language are current:

```
mcp__plugin_mcp-hub_mcp-hub__get_page(page_id="210206856")
```

The returned `body_storage` is Confluence storage XHTML — that is what you will reuse for the new page. The placeholder prompts inside it (italic guidance, `<ac:placeholder>` blocks) describe what each section is for.


## 2. Draft the copy with the user

Work iteratively. For each section the user has content for, propose Confluence storage XHTML (the same format the template uses). Keep all section headings even when a section is short — engineers scanning the doc rely on the rhythm. Use `*na*` when a section truly doesn't apply (e.g. *Current state* for a greenfield effort).


A few light style notes from existing docs:
- For a quick-turnaround effort, prepend the `info` banner above under the TOC. See *Hosting images* for the canonical wording.
- The *User interaction and design* section is the workhorse — add h3 subheadings freely (*Discussion*, *Potential solutions*, *Decision*, *Use cases*, *Class diagram*). *Results Processing* is a good example of the depth this section can take.
- Story points use the `X.01` convention (`1.01`, `2.01`, …) in the Tasks table.

Show the user the draft. **Do not call `create_page` until the user explicitly approves the copy.**

## 3. Write to Confluence

Once the user approves, create the page directly:

```
mcp__plugin_mcp-hub_mcp-hub__create_page(
    space="SSE",
    parent_id="176589711",
    title="<doc title>",
    body=<full storage-format XHTML, headings + content>,
)
```

For subsequent edits, use `mcp__plugin_mcp-hub_mcp-hub__update_page` — prefer `mode="replace_section"` with the heading name when revising one section, and pass `expected_version` from a fresh `get_page` to avoid clobbering concurrent edits.

This skill does not transition the page status, review the doc, or notify reviewers. Leave it in DRAFT and let the user circulate it.
