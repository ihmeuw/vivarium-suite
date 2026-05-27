# Visual Companion Guide

Browser-based diagramming companion for showing **structural diagrams** during a brainstorm — class, sequence, state, data-flow, ER, architecture, flowchart — via [Mermaid](https://mermaid.js.org/). UI-mockup helpers are also available but rarely needed for SimSci work.

## When to Use

Decide per-question, not per-session. The test: **would the user understand this better by seeing it than reading it?**

**Use the browser** when the content itself is structural:

- **Class / object structure** — comparing decomposition options (inheritance vs composition, where state lives, what an interface boundary looks like)
- **Sequence diagrams** — control flow across components, async/await ordering, who-calls-whom
- **State machines** — finite-state designs, lifecycle transitions, status flows
- **Data-flow / pipeline diagrams** — how a value moves through stages, what each stage owns
- **ER / data-model diagrams** — table relationships, foreign-key shapes, cardinality
- **Architecture diagrams** — system components, services, deployment shape
- **Side-by-side structure comparisons** — "approach A's class graph vs approach B's"
- **Option grids of diagrams** — A/B/C where each option is a small diagram

**Use the terminal** when the content is text or tabular:

- **Requirements and scope questions** — "what does X mean?", "which features are in scope?"
- **Conceptual A/B/C choices** — picking between approaches described in words
- **Tradeoff lists** — pros/cons, comparison tables
- **Concept-level decisions** — "should this be a state machine or just a boolean flag?" (this is conceptual; the *next* question, "here's the state machine — do these transitions look right?", is visual)

A question *about* a structural topic is not automatically visual. Picking between concepts is text; inspecting the shape is visual.

## How It Works

The server watches a directory for HTML files and serves the newest one to the browser. You write HTML content to `screen_dir`, the user sees it in their browser and can click to select options. Selections are recorded to `state_dir/events` that you read on your next turn.

**Content fragments vs full documents:** If your HTML file starts with `<!DOCTYPE` or `<html`, the server serves it as-is (just injects the helper script). Otherwise, the server wraps your content in the frame template — adding the header, CSS theme, **Mermaid library**, selection indicator, and all interactive infrastructure. **Write content fragments by default.** Only write full documents when you need complete control over the page.

## Diagramming with Mermaid

Mermaid is loaded into every frame-template page. To render a diagram, write a `<pre class="mermaid">` block with [Mermaid syntax](https://mermaid.js.org/intro/) inside your content fragment:

```html
<h2>Which class structure fits better?</h2>
<p class="subtitle">Same behavior, different decomposition</p>

<div class="split">
  <div>
    <h3>A — Inheritance</h3>
    <pre class="mermaid">
classDiagram
  Animal <|-- Dog
  Animal <|-- Cat
  Animal : +eat()
    </pre>
  </div>
  <div>
    <h3>B — Composition</h3>
    <pre class="mermaid">
classDiagram
  class Dog { -Eater eater }
  class Cat { -Eater eater }
  class Eater { +eat() }
  Dog --> Eater
  Cat --> Eater
    </pre>
  </div>
</div>
```

Any Mermaid diagram type works — `classDiagram`, `sequenceDiagram`, `stateDiagram-v2`, `erDiagram`, `flowchart LR`, `gantt`, `gitGraph`, and more. Mermaid auto-renders on page load and respects the OS dark/light theme.

**Tips for legible Mermaid:**

- One diagram per concept. If a single diagram needs scrolling to read, split it into two side-by-side panels via `<div class="split">`.
- Label relationships. `Dog --> Eater : delegates to` is more informative than `Dog --> Eater`.
- Avoid syntax that needs Mermaid plugins — keep to core syntax so the CDN script handles it.
- The leading/trailing whitespace inside `<pre class="mermaid">` matters for some diagram types — keep the directive (`classDiagram`, etc.) at column 0 of its line.

## Starting a Session

```bash
# Start server with persistence (diagrams saved to project)
scripts/start-server.sh --project-dir /path/to/project

# Returns: {"type":"server-started","port":52341,"url":"http://localhost:52341",
#           "screen_dir":"/path/to/project/.brainstorm/12345-1706000000/content",
#           "state_dir":"/path/to/project/.brainstorm/12345-1706000000/state"}
```

Save `screen_dir` and `state_dir` from the response. Tell user to open the URL.

**Finding connection info:** The server writes its startup JSON to `$STATE_DIR/server-info`. If you launched the server in the background and didn't capture stdout, read that file to get the URL and port. When using `--project-dir`, check `<project>/.brainstorm/` for the session directory.

**Note:** Pass the project root as `--project-dir` so diagrams persist in `.brainstorm/` and survive server restarts. Without it, files go to `/tmp` and get cleaned up. Remind the user to add `.brainstorm/` to `.gitignore` if it's not already there.

**Launching the server by platform:**

**Claude Code (macOS / Linux):**
```bash
# Default mode works — the script backgrounds the server itself
scripts/start-server.sh --project-dir /path/to/project
```

**Claude Code (Windows):**
```bash
# Windows auto-detects and uses foreground mode, which blocks the tool call.
# Use run_in_background: true on the Bash tool call so the server survives
# across conversation turns.
scripts/start-server.sh --project-dir /path/to/project
```
When calling this via the Bash tool, set `run_in_background: true`. Then read `$STATE_DIR/server-info` on the next turn to get the URL and port.

**Codex:**
```bash
# Codex reaps background processes. The script auto-detects CODEX_CI and
# switches to foreground mode. Run it normally — no extra flags needed.
scripts/start-server.sh --project-dir /path/to/project
```

**Gemini CLI:**
```bash
# Use --foreground and set is_background: true on your shell tool call
# so the process survives across turns
scripts/start-server.sh --project-dir /path/to/project --foreground
```

**Other environments:** The server must keep running in the background across conversation turns. If your environment reaps detached processes, use `--foreground` and launch the command with your platform's background execution mechanism.

If the URL is unreachable from your browser (common in remote/containerized setups), bind a non-loopback host:

```bash
scripts/start-server.sh \
  --project-dir /path/to/project \
  --host 0.0.0.0 \
  --url-host localhost
```

Use `--url-host` to control what hostname is printed in the returned URL JSON.

## The Loop

1. **Check server is alive**, then **write HTML** to a new file in `screen_dir`:
   - Before each write, check that `$STATE_DIR/server-info` exists. If it doesn't (or `$STATE_DIR/server-stopped` exists), the server has shut down — restart it with `start-server.sh` before continuing. The server auto-exits after 30 minutes of inactivity.
   - Use semantic filenames: `class-diagram.html`, `sequence-flow.html`, `state-machine.html`, `data-flow.html`
   - **Never reuse filenames** — each screen gets a fresh file
   - Use Write tool — **never use cat/heredoc** (dumps noise into terminal)
   - Server automatically serves the newest file

2. **Tell user what to expect and end your turn:**
   - Remind them of the URL (every step, not just first)
   - Give a brief text summary of what's on screen (e.g., "Showing two class-structure options for the loader")
   - Ask them to respond in the terminal: "Take a look and let me know what you think. Click to select an option if you'd like."

3. **On your next turn** — after the user responds in the terminal:
   - Read `$STATE_DIR/events` if it exists — this contains the user's browser interactions (clicks, selections) as JSON lines
   - Merge with the user's terminal text to get the full picture
   - The terminal message is the primary feedback; `state_dir/events` provides structured interaction data

4. **Iterate or advance** — if feedback changes current screen, write a new file (e.g., `class-diagram-v2.html`). Only move to the next question when the current step is validated.

5. **Unload when returning to terminal** — when the next step doesn't need the browser (e.g., a clarifying question, a tradeoff discussion), push a waiting screen to clear the stale content:

   ```html
   <!-- filename: waiting.html (or waiting-2.html, etc.) -->
   <div style="display:flex;align-items:center;justify-content:center;min-height:60vh">
     <p class="subtitle">Continuing in terminal...</p>
   </div>
   ```

   This prevents the user from staring at a resolved choice while the conversation has moved on. When the next visual question comes up, push a new content file as usual.

6. Repeat until done.

## Writing Content Fragments

Write just the content that goes inside the page. The server wraps it in the frame template automatically (header, theme CSS, Mermaid library, selection indicator, and all interactive infrastructure).

**Minimal diagram example:**

```html
<h2>Which sequence works better?</h2>
<p class="subtitle">Same outcome, different ordering</p>

<div class="options">
  <div class="option" data-choice="a" onclick="toggleSelect(this)">
    <div class="letter">A</div>
    <div class="content">
      <h3>Validate before fetch</h3>
      <pre class="mermaid">
sequenceDiagram
  Client->>Validator: check(input)
  Validator-->>Client: ok
  Client->>API: fetch(input)
  API-->>Client: data
      </pre>
    </div>
  </div>
  <div class="option" data-choice="b" onclick="toggleSelect(this)">
    <div class="letter">B</div>
    <div class="content">
      <h3>Fetch then validate</h3>
      <pre class="mermaid">
sequenceDiagram
  Client->>API: fetch(input)
  API-->>Client: data
  Client->>Validator: check(data)
  Validator-->>Client: ok
      </pre>
    </div>
  </div>
</div>
```

That's it. No `<html>`, no CSS, no `<script>` tags needed. The server provides all of that, including Mermaid.

## CSS Classes Available

The frame template provides these CSS classes for your content:

### Options (A/B/C choices)

```html
<div class="options">
  <div class="option" data-choice="a" onclick="toggleSelect(this)">
    <div class="letter">A</div>
    <div class="content">
      <h3>Title</h3>
      <p>Description or diagram</p>
    </div>
  </div>
</div>
```

**Multi-select:** Add `data-multiselect` to the container to let users select multiple options. Each click toggles the item. The indicator bar shows the count.

```html
<div class="options" data-multiselect>
  <!-- same option markup — users can select/deselect multiple -->
</div>
```

### Split view (side-by-side)

The workhorse for "approach A vs approach B" diagram comparisons.

```html
<div class="split">
  <div><h3>A</h3><pre class="mermaid">...</pre></div>
  <div><h3>B</h3><pre class="mermaid">...</pre></div>
</div>
```

### Cards (grid of choices with title + body)

```html
<div class="cards">
  <div class="card" data-choice="approach1" onclick="toggleSelect(this)">
    <div class="card-image"><!-- a small diagram or icon --></div>
    <div class="card-body">
      <h3>Name</h3>
      <p>Description</p>
    </div>
  </div>
</div>
```

### Pros/Cons

```html
<div class="pros-cons">
  <div class="pros"><h4>Pros</h4><ul><li>Benefit</li></ul></div>
  <div class="cons"><h4>Cons</h4><ul><li>Drawback</li></ul></div>
</div>
```

### Typography and sections

- `h2` — page title
- `h3` — section heading
- `.subtitle` — secondary text below title
- `.section` — content block with bottom margin
- `.label` — small uppercase label text

### Also available (UI-mockup classes)

The frame template carries some leftover CSS for UI wireframes — `.mockup` / `.mockup-header` / `.mockup-body` for framed mock containers, `.mock-nav` / `.mock-sidebar` / `.mock-content` / `.mock-button` / `.mock-input` for inline wireframe building blocks, and `.placeholder` for empty regions. They work if you need them, but for SimSci work you almost never will. See `scripts/frame-template.html` for the styling.

## Browser Events Format

When the user clicks options in the browser, their interactions are recorded to `$STATE_DIR/events` (one JSON object per line). The file is cleared automatically when you push a new screen.

```jsonl
{"type":"click","choice":"a","text":"Option A - Inheritance","timestamp":1706000101}
{"type":"click","choice":"c","text":"Option C - Composition with adapter","timestamp":1706000108}
{"type":"click","choice":"b","text":"Option B - Pure composition","timestamp":1706000115}
```

The full event stream shows the user's exploration path — they may click multiple options before settling. The last `choice` event is typically the final selection, but the pattern of clicks can reveal hesitation or preferences worth asking about.

If `$STATE_DIR/events` doesn't exist, the user didn't interact with the browser — use only their terminal text.

## Design Tips

- **One concept per diagram.** A class diagram showing both inheritance *and* call flow is hard to read; split into a `classDiagram` and a `sequenceDiagram` side by side instead.
- **Label the relationships.** `Dog --> Eater : delegates to` and `User --> Order : places` are far easier to read than bare arrows. Same for sequence-diagram messages.
- **Explain the question on each page** — "Which decomposition fits better?" not just "Pick one".
- **Iterate before advancing** — if feedback changes current screen, write a new version (`class-diagram-v2.html`).
- **Side-by-side for structural alternatives.** `<div class="split">` with one Mermaid block per side is the default shape for "approach A vs approach B" questions.
- **2-4 options max** per screen.
- **Keep diagrams to the parts that matter.** Don't render the whole codebase when the question is about one boundary.

## File Naming

- Use semantic names: `class-diagram.html`, `sequence-flow.html`, `state-machine.html`, `data-flow.html`, `er-diagram.html`, `architecture.html`
- Never reuse filenames — each screen must be a new file
- For iterations: append version suffix like `class-diagram-v2.html`, `class-diagram-v3.html`
- Server serves newest file by modification time

## Cleaning Up

```bash
scripts/stop-server.sh $SESSION_DIR
```

If the session used `--project-dir`, diagram files persist in `.brainstorm/` for later reference. Only `/tmp` sessions get deleted on stop.

## Reference

- Frame template (CSS reference + Mermaid loader): `scripts/frame-template.html`
- Helper script (client-side): `scripts/helper.js`
- Mermaid syntax docs: https://mermaid.js.org/intro/
