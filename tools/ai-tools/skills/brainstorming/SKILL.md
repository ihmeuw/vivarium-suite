---
name: brainstorming
description: Use BEFORE any design or feature work — picking an approach, scoping a change, fleshing out a sparse ticket, deciding whether something needs a design doc. Runs a structured brainstorm with one-question-at-a-time clarification, 2–3 approaches with explicit tradeoffs, ultimately producing a design artifact such as design-doc or JIRA ticket. Trigger on any task that involves reasoning about *what* to build or *how* to approach it, but not when the design is already settled and only execution remains.
---

# Brainstorming

Turn an idea — or ticket — into a verified design as one of three real artifacts:

| Tier | Artifact | Where it lands |
|------|----------|----------------|
| **(a) Plan comment** | A structured plan, posted on an existing Jira ticket | Jira (`add_comment`) |
| **(b) New ticket** | A team-conventions-shaped ticket with the plan detail in a `Notes` section of the description | Jira (`create_issue`) |
| **(c) Full design** | A Confluence design doc, delegated to the `design-doc` skill | Confluence (`create_page`) |

You produce the artifact directly via MCP after explicit user approval.

Do NOT invoke any implementation skill, write any code, scaffold any project, or take any implementation action until an artifact has been written to Jira or Confluence and the user has confirmed it. This applies to every project regardless of perceived simplicity.

## When to use

Trigger whenever the user is reasoning about *what* to build or *how* to approach it — not when the design is already settled and only execution remains. Specifically:

- "I have MIC-#### but the ticket is one sentence — help me flesh it out."
- "We should build / change / add …" with no agreed shape yet.
- "What's the best way to …" or "should we use X or Y …"
- The user gestures at a problem ("X is slow", "Y is confusing") without specifying a fix.

Skip when the design is already pinned down and the next move is just to write code.

## Input detection

If the user message contains a Jira key (`MIC-####`), fetch the ticket and use its description, comments, and linked hub pages as the starting context for the brainstorm. If there’s no ticket key, treat it as an idea-only brainstorm and start with a blank slate.

## Process

You **MUST** track these as tasks (via TaskCreate) and complete in order:

1. **Detect input.** Pull the ticket via `get_issue` if a `MIC-####` key was provided; otherwise it's an idea-only brainstorm.
2. **Get the surrounding context.** Read related files, recent commits, any hub pages linked from the ticket. State what you found in one short paragraph before asking anything.
3. **Scope check.** If the request is really multiple independent subsystems ("a platform with chat, billing, analytics"), say so now and help decompose. Brainstorm only the first piece. Don't burn questions refining a project that needs to be split.
4. **Offer the Visual Companion** *only if* upcoming questions involve **structural diagrams** — class / sequence / state / data-flow / architecture / ER comparisons. UI mockups are supported but rarely needed for SimSci work. This offer is **its own message** — no clarifying question, no context summary appended. See [Visual Companion](#visual-companion) below.
5. **Clarify, one question at a time.** Multiple choice when possible; open-ended is fine when not. Each question stands alone — no stacked sub-questions. Focus on purpose, constraints, success criteria, non-goals.
6. **Propose 2–3 approaches** with tradeoffs. Lead with the recommended one and say why. Don't pad the list — three real options beat two real + one strawman.
7. **Walk the design in sections.** Architecture, components, data flow, error handling, testing — scale each section to its complexity (one sentence for simple, ~200 words for nuanced). Ask after each section: *"Look right so far?"* Be ready to back up and revise.
8. **Routing checkpoint.** State a one-sentence scope read-out, recommend a tier, and let the user override. See [Routing checkpoint](#routing-checkpoint) below.
9. **Produce the artifact** for the chosen tier. See [Producing the artifact](#producing-the-artifact) below.
10. **Report.** Print the artifact's URL or Jira key. Stop. Don't chain into branch setup or implementation — the user invokes those explicitly if they want to.

## Routing checkpoint

Once the design walk is done, surface the scope read-out and the recommendation as one message:

> Based on what we've worked through, this looks like roughly **\<one-sentence sizing\>** — \<e.g. one developer session / one ticket's worth / a multi-week design with sub-tickets\>. I'd recommend **tier (X): \<artifact summary\>**. Want to go with that, or pick a different tier?

Validation rules:

- If the user picks tier (a) and there's no input ticket, push back and ask whether they meant to provide a ticket key or whether the brainstorm should produce a new ticket (b) instead.
- If the user picks tier (b) and there *is* an input ticket, push back: tier (b) creates a new ticket, but they already have one. Offer (a) (comment on the existing ticket) or (c) (design doc that references the existing ticket).
- Tier (c) is valid in both cases — the design doc can reference an existing ticket or stand alone.

## Producing the artifact

### Tier (a) — plan comment on existing ticket

Compose a Jira-wiki-markup comment body with these sections:

- `h3. Scope` — one paragraph on what's in / out.
- `h3. Approach` — the chosen approach, with a brief mention of what was considered and rejected.
- `h3. Acceptance criteria` — bulleted, testable.
- `h3. Notes / risks` — anything the implementer should know that doesn't fit above.

Show the user the comment body and the target ticket key. On explicit approval, write it.


### Tier (b) — new ticket with Notes section

Invoke the `team-conventions` skill to format the skill. The team-conventions structure is the spine — *keep the ticket description short and descriptive* per that template. You may optionally append a "Notes" section *inside the description* for extra detail that doesn't fit the main template; only use this if actually necessary.

Show the user the full ticket payload (summary, description, type, labels, project). On explicit approval, create the ticket.


### Tier (c) — full design doc

Hand off to the `design-doc` skill with the brainstorm output as input. The design-doc skill owns the rest.

## Working in existing code

- Explore the current structure before proposing changes. Follow existing patterns.
- If existing code has problems that affect the work (file too large, blurry boundaries, tangled responsibilities), fold *targeted* improvements into the design — the way a good developer cleans up code they're already touching.
- Don't propose unrelated refactoring. Stay focused on what serves the current goal.

## Design for isolation

Break the system into units that each have one clear purpose, communicate through well-defined interfaces, and can be understood and tested independently. For each unit you should be able to answer: *what does it do, how do you use it, what does it depend on?*

If a reader can't understand what a unit does without reading its internals — or you can't change the internals without breaking consumers — the boundaries need work. Large files are usually a signal that a unit is doing too much; same goes for your edits being unreliable when the file gets too big for one context window.

## Key disciplines

- **One question at a time.** Don't stack. If a topic needs more, ask the next one next turn.
- **Multiple choice first.** Faster for the user, forces you to actually have options in mind.
- **YAGNI ruthlessly.** Strip features the user didn't ask for. The design gets *smaller* as it gets clearer.
- **Recommend, don't punt.** Present alternatives, but lead with your pick and the reason.
- **Incremental approval.** Section by section, not one giant wall.
- **Be willing to back up.** When something doesn't fit, the answer is to revise, not to keep going.
- **Approve before writing.** No MCP write call goes out without an explicit user yes. Show the payload, wait, then write.

## Visual Companion

A browser-based companion for showing **structural diagrams** during a brainstorm — class, sequence, state, data-flow, ER, architecture, flowchart — via Mermaid. UI mockups are also supported but rarely useful for our work. **It's a tool, not a mode.** Accepting it means it's *available* when a question benefits from a picture — it doesn't mean every question goes through the browser.

**Offering it (its own message, nothing else):**

> Some of what we're about to work through might be easier to show than describe — class diagrams, sequence diagrams, state machines, and other structural views. I can spin up a local browser companion that renders Mermaid diagrams for those questions. It's still pretty new and uses extra tokens. Want to try it? (Requires opening a local URL.)

Wait for the user's answer before continuing. If they decline, proceed text-only.

**Per-question decision (even after they accept):** would the user understand this better by *seeing* it than by *reading* it?

- **Browser** for content that *is* structural — class diagrams comparing decomposition options, sequence diagrams for control flow, state machines, data-flow / ER diagrams, architecture diagrams, side-by-side structure comparisons.
- **Terminal** for content that's text — requirements questions, conceptual A/B/C choices, tradeoff lists, scope decisions.

A question *about* a structural topic is not automatically visual. *"Should this be a state machine or just a flag?"* — terminal (you're picking between concepts). *"Here's the state machine — does this transition set look right?"* — browser (you're inspecting structure).

If the user accepts, read [visual-companion.md](visual-companion.md) before using the companion — it covers the server, screen lifecycle, CSS classes, and event format.

---

*Heavily adapted from [obra/superpowers' brainstorming skill](https://github.com/obra/superpowers/tree/main/skills/brainstorming). The one-question-at-a-time discipline, the 2–3-approach format, and the visual companion are all from there. The tiered routing, MCP write integration, Mermaid-first diagramming, and `team-conventions` / `design-doc` handoffs are SimSci-specific.*
