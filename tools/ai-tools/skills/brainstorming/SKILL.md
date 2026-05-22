---
name: brainstorming
description: Use BEFORE any design or feature work — building a component, adding functionality, modifying behavior, picking an approach. Runs a structured brainstorm: background search, one-question-at-a-time clarification, 2–3 approaches with explicit tradeoffs, then a verified design that becomes a Jira-wiki-markup ticket draft. Trigger on "brainstorm", "design", "spec out", "let's figure out how to", "what are our options for", or any request to plan a change before writing code.
---

# Brainstorming

Turn an idea into a verified design and a Jira ticket draft, through one-question-at-a-time dialogue. Output is a ticket body the user pastes into Jira — not a markdown spec, not code.

<HARD-GATE>
Do NOT invoke any implementation skill, write any code, scaffold any project, or take any implementation action until the user has approved a design and pasted the resulting ticket draft into Jira. This applies to every project regardless of perceived simplicity. "It's just a config change" is when unexamined assumptions cost the most.
</HARD-GATE>

## When to use

Trigger whenever the user is reasoning about *what* to build or *how* to approach it — not when they've already decided and want the change made. Specifically:

- "We should build / change / add …" with no agreed shape yet.
- "What's the best way to …" or "should we use X or Y …"
- A ticket exists but is one sentence and the design isn't pinned down.
- The user gestures at a problem ("X is slow", "Y is confusing") without specifying a fix.

Skip when the design is already settled and only execution remains. The HARD-GATE is for the design step, not every code change in the world.

## Process

You **MUST** track these as tasks (via TaskCreate) and complete in order:

1. **Get the context.** Read related files, recent commits, the Jira ticket if one exists (`mcp__plugin_mcp-jira_mcp-jira__get_issue`), and linked design docs on the hub. State what you found in one short paragraph before asking anything.
2. **Scope check.** If the request is really multiple independent subsystems ("a platform with chat, billing, analytics"), say so now and help decompose. Brainstorm only the first piece. Don't burn questions refining a project that needs to be split.
3. **Offer the Visual Companion** *only if* upcoming questions involve visual content (mockups, layout, diagrams). This offer is **its own message** — no clarifying question, no context summary appended. See [Visual Companion](#visual-companion) below.
4. **Clarify, one question at a time.** Multiple choice when possible; open-ended is fine when not. Each question stands alone — no stacked sub-questions. Focus on purpose, constraints, success criteria, non-goals.
5. **Propose 2–3 approaches** with tradeoffs. Lead with the recommended one and say why. Don't pad the list — three real options beat two real + one strawman.
6. **Present the design in sections.** Architecture, components, data flow, error handling, testing — scale each section to its complexity (one sentence for simple, ~200 words for nuanced). Ask after each section: *"Look right so far?"* Be ready to go back and revise.
7. **Draft the Jira ticket.** Invoke the `team-conventions` skill (§2) to format the validated design as a Jira-wiki-markup ticket against the hub template (Confluence page 178128092). The brainstorm produces *the content* of the ticket; team-conventions produces *the structure*.
8. **Self-review the draft** with the spec-document-reviewer subagent — see [Spec self-review](#spec-self-review) below. Fix issues inline. No re-review after fix.
9. **Show the draft to the user.** They paste it into Jira. Ask for the new MIC-#### key.
10. **Hand off.** Once the ticket exists, invoke the `team-conventions` skill (§1) to set up the branch. Then stop. Implementation is a separate session.

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

## Spec self-review

After the team-conventions draft is generated and *before* showing the user, dispatch a subagent for a fresh-eyes review. Use the [spec-document-reviewer-prompt.md](spec-document-reviewer-prompt.md) template in this skill — it expects the Jira-wiki-markup ticket text inline (not a file path, since we don't write a spec file).

Fix anything the reviewer flags inline. No need to re-run the review. Then show the user.

## After the ticket exists

- **Done with brainstorming.** Don't invoke any implementation skill from here. Implementation belongs in a new session, on a fresh branch, with the ticket in hand.
- If the user wants to keep going *in this session*, that's their call — but the brainstorming skill itself ends at the branch handoff.

## Anti-pattern: "this is too simple to need a design"

Every change goes through this. A one-line config edit, a renamed variable, a one-shot script — all of them. The design can be three sentences for a trivial change, but you must state it and get a thumbs-up before touching code. Skipping the gate is how scope creep and unexamined assumptions sneak in.

## Visual Companion

A browser-based companion for showing mockups, diagrams, and visual options during a brainstorm. **It's a tool, not a mode.** Accepting it means it's *available* when a question benefits from a picture — it doesn't mean every question goes through the browser.

**Offering it (its own message, nothing else):**

> Some of what we're about to work through might be easier to show than describe — mockups, layout comparisons, diagrams. I can spin up a local browser companion for those questions. It's still pretty new and uses extra tokens. Want to try it? (Requires opening a local URL.)

Wait for the user's answer before continuing. If they decline, proceed text-only.

**Per-question decision (even after they accept):** would the user understand this better by *seeing* it than by *reading* it?

- **Browser** for content that *is* visual — mockups, wireframes, layout comparisons, architecture diagrams, side-by-side visual designs.
- **Terminal** for content that's text — requirements questions, conceptual A/B/C choices, tradeoff lists, scope decisions.

A question *about* a UI topic is not automatically visual. *"What does personality mean here?"* — terminal. *"Which of these wizard layouts works better?"* — browser.

If the user accepts, read [visual-companion.md](visual-companion.md) before using the companion — it covers the server, screen lifecycle, CSS classes, and event format.

---

*Heavily adapted from [obra/superpowers' brainstorming skill](https://github.com/obra/superpowers/tree/main/skills/brainstorming). The HARD-GATE pattern, the one-question-at-a-time discipline, the 2–3-approach format, the visual companion, and the spec self-review subagent are all from there.*
