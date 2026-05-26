---
name: vivarium-research
description: Search and retrieve content from the Vivarium Research documentation (vivarium-research.readthedocs.io). Use whenever the user asks, implicitly or explicitly, the specification for how something is modelled in Vivarium — a disease, risk factor, intervention, demographic process, or simulation-component design — or when they reference the Vivarium Research docs by name or URL.
---

# Vivarium Research connector

The Vivarium Research documentation (https://vivarium-research.readthedocs.io/en/latest/) is the canonical source for Vivarium *modelling strategy*: how the team represents specific diseases, risk factors, interventions, and simulation components in code. It complements the Vivarium framework API docs, which describe how to *use* the simulation engine.

## Workflow

1. **Discover the doc tree** when you need a navigation overview (which top-level sections exist, which model categories are available, etc.). The Sphinx sidebar on the root page lists every section as a `<dir>/index.html` link, so:

   ```bash
   curl -s 'https://vivarium-research.readthedocs.io/en/latest/' \
     | grep -oE 'href="[^"#]+/index\.html"' \
     | sort -u
   ```

2. **Search the docs** via the Read the Docs v2 search API:

   ```bash
   curl -s 'https://readthedocs.org/api/v2/search/?q=<QUERY>&project=vivarium-research&version=latest'
   ```

   The response is JSON: `{count, results: [{title, path, domain, blocks: [{title, content, highlights}], ...}]}`. Each `block` is a section of the matching page with the surrounding text — read the blocks first; the answer is often already in the snippet.

3. **Fetch the full page** with WebFetch on `<domain><path>` only when the snippets don't answer the question. Pass a focused prompt naming the section you need.

4. **Cite source URLs** so the user can verify against the canonical doc.

## Notes

- The newer `/_/api/v3/search/` endpoint returns empty for this project — stick with v2 at `readthedocs.org/api/v2/search/`.
- Search is keyword-based. If the first query returns no useful hits, re-query with synonyms, abbreviations (e.g. "TB" vs "tuberculosis"), or GBD terminology.
- Do not invent details. If the docs don't cover something, say so.
