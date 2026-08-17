---
name: ticket-triage
description: Use after a code review when some findings won't be addressed in the current PR — triages them into Jira ticket recommendations. Classifies each unaddressed finding (address-now / ticket / drop), groups ticket candidates by theme, checks the MIC backlog for duplicates via the `_duplicate_finder` sub-agent, then drafts and files tickets per team conventions, every write gated on explicit user approval. Trigger on "triage the findings", "file tickets for the rest", "turn these review comments into tickets", or when the user defers review findings after `/simsci:pr-prep`.
---

# Ticket triage

Turn the leftover findings of a code review — the ones that are real but out of
scope for the current PR — into vetted, deduplicated Jira ticket
recommendations, and file the ones the user approves.


## Process

1. **Collect.** Gather every finding from the session's review, normalized as:
   review agent (design / maintainability / DRY / tests / documentation /
   functionality), file references, a one-sentence description, and the
   evidence behind it.
2. **Draw the scope line.** Ask the user which findings they are addressing in
   the current PR. Those are tagged *address now* and excluded from ticketing.
   If everything is being addressed, report there is nothing to triage and
   stop. **If the caller handed over a pre-scoped set** — stating explicitly that
   the user has already decided what is being addressed in this PR — take it as
   given and go straight to step 3. Asking again re-opens a decision the user
   already made.
3. **Classify the rest.** Each remaining finding is either a *ticket
   candidate* or a *drop* (stylistic, speculative, or too trivial to be worth
   backlog space). Every drop gets a one-line reason. **No silent drops** —
   always show the drop list, and promote any drop back to candidate if the
   user asks.
4. **Group candidates by theme.** Findings that touch the same subsystem or
   share a root cause travel together as one proposed ticket; a significant
   standalone finding (e.g. a confirmed logic bug) gets its own. Name each
   group with a working ticket title.
5. **Check for duplicates.** Spawn the `_duplicate_finder` sub-agent (one
   call, all groups) with, per group: the working title, a one-paragraph
   digest, and distinctive search terms (component names, symbols, file
   paths, plain-English phrases). It returns at most five compact matches per
   group — key, summary, status, confidence, why — plus the queries it tried.
   If the sub-agent fails or the Jira MCP is unavailable, continue with the
   affected groups flagged **"dedup not run"**; never block on it.
6. **Present the triage report.** For each proposed ticket: title, member
   findings, dedup matches (if any), and your recommendation. Below that, the
   drop list with reasons. Then ask the user to disposition each proposed
   ticket: **file** it, **skip** it, or **comment on an existing ticket**
   instead (when a dedup match already covers it).
7. **Draft and file.** For each *file* disposition, follow the
   `team-conventions` skill §2 ("Writing a Jira ticket") end to end — it owns
   the hub ticket policy, the wiki-markup draft, the show-the-payload
   approval gate, and the create call; don't re-derive any of that here. On
   top of §2, two triage-specific defaults: set the issue type from the
   group's content (Bug for a confirmed defect, Story otherwise — the user
   can override), and if you can tell which epic the work belongs to, name it
   in the draft so the user can place the ticket in that epic's backlog after
   creation (`create_issue` has no epic field; board placement stays manual
   per the hub policy). For a *comment* disposition, draft the comment, show
   it, and on approval post it.
8. **Report.** List the filed ticket keys/URLs, the comments posted, the
   skips, and the drops.

## Key disciplines

- **Nothing is written without approval.** Every `create_issue` and
  `add_comment` call is preceded by the exact payload on screen and an
  explicit user yes.
- **Never fill in the TL;DR on your own.** It is the user's to write. Either
  use *exactly* their own words or leave §2's `_TODO (human)...` line in place
  and tell them it is waiting on them. Being told to fill in every section
  does not include this one.
- **Dedup is advisory.** A match is shown next to the draft; it never
  silently suppresses a recommendation — the user decides.
- **No silent drops.** Anything excluded from ticketing appears in the drop
  list with a reason.
- **Stay out of the review's lane.** Do not re-litigate or re-run the review;
  triage works with the findings as given. Findings the user is fixing in the
  PR are not ticket material.
- **Degrade loudly.** If the hub or Jira MCP is unavailable, present the
  drafts anyway and follow §2's fallback — link the user to the hub policy
  page so they can file manually, rather than improvising a substitute
  filing path.
