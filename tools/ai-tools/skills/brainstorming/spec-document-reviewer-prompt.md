# Ticket-Draft Reviewer Prompt Template

Use this template when dispatching a subagent to review the Jira-wiki-markup ticket draft produced at the end of a brainstorm.

**Purpose:** Catch placeholders, contradictions, ambiguity, scope creep, and Jira-wiki syntax issues before the user pastes the draft into Jira.

**Dispatch after:** The ticket draft has been produced via the `team-conventions` skill (§2) and *before* showing it to the user.

**Note on input:** Pass the full ticket draft *inline* in the subagent prompt. There is no spec file on disk — the brainstorm output is meant to live in Jira, not in the repo.

```
Agent tool (general-purpose):
  description: "Review brainstorm ticket draft"
  prompt: |
    You are a ticket-draft reviewer. Verify this Jira ticket body is complete and ready
    for the user to paste into Jira.

    **Ticket draft (Jira wiki markup):**

    ```
    [PASTE THE FULL DRAFT HERE]
    ```

    **Source brainstorm context (one paragraph, what was decided):**
    [BRIEF SUMMARY OF THE BRAINSTORM CONCLUSIONS]

    ## What to check

    | Category      | What to look for                                                                                 |
    |---------------|--------------------------------------------------------------------------------------------------|
    | Completeness  | "TBD", "TODO", empty sections, placeholders the brainstorm should have resolved                  |
    | Consistency   | Internal contradictions (e.g. acceptance criteria conflict with the description)                  |
    | Clarity       | Requirements ambiguous enough that two readers would build different things                     |
    | Scope         | Multiple independent subsystems mashed into one ticket — should be decomposed                    |
    | YAGNI         | Features that weren't asked for, gold-plating, premature abstractions                            |
    | Jira syntax   | Markdown leaking through (`**bold**`, `#headings`, ` ```fenced``` `) where Jira-wiki is expected |
    | Hub fidelity  | Sections from hub page 178128092 ("Make a Jira Ticket") missing or renamed                       |

    ## Calibration

    Only flag issues that would cause real problems — a missing acceptance criterion, a contradiction,
    a requirement so vague it could be interpreted two different ways, Markdown that won't render in
    Jira. Minor wording preferences and "sections are uneven" are not issues.

    Approve unless there are serious gaps that would lead to a flawed ticket.

    ## Output format

    ## Ticket Review

    **Status:** Approved | Issues Found

    **Issues (if any):**
    - [Section]: [specific issue] — [why it matters before paste]

    **Recommendations (advisory, do not block approval):**
    - [suggestions for improvement]
```

**Reviewer returns:** Status, Issues (if any), Recommendations. Fix flagged issues inline in the draft, then show it to the user. Do not re-run the review after fixing.
