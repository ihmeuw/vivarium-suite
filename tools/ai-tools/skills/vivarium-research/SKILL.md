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

2. **Search the docs** via the Read the Docs v3 server-side search API. The project slug is embedded in the `q` parameter:

   ```bash
   curl -s 'https://app.readthedocs.org/api/v3/search/?q=project:vivarium-research%20<QUERY>'
   ```

   The response is JSON: `{count, projects, results: [{title, path, domain, blocks: [{title, content, highlights}], ...}]}`. Each `block` is a section of the matching page with the surrounding text — read the blocks first; the answer is often already in the snippet.

3. **Fetch the full page** with WebFetch on `<domain><path>` only when the snippets don't answer the question. Pass a focused prompt naming the section you need.

4. **Cite source URLs** so the user can verify against the canonical doc.

## Notes

- Search is keyword-based. If the first query returns no useful hits, re-query with synonyms, abbreviations (e.g. "TB" vs "tuberculosis"), or GBD terminology.
- Do not invent details. If the docs don't cover something, say so.
- If you are working on a model repository, you most likely want to understand how the relevant component is modelled in *that specific concept model*. The concept models
for active model repositories are documented in models/concept_models/index.html. You should be able to determine the relevant concept model based on the model repository name and description, but if not, ask the user to clarify which concept model they want to reference.
