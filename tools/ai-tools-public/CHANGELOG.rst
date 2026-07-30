**0.2.0 - 07/28/26**

 - **Breaking:** ``/simsci:code-reviewer`` is removed and replaced by
   ``/simsci:pr-prep`` (MIC-7282). The review itself is unchanged — same five-lens
   fan-out, same confidence scoring — but it is now the opening step of a loop that
   carries the change through to a PR rather than an endpoint. There is no
   review-only entry point: in practice a review is always followed by acting on
   the findings, so the two are one workflow. Migration: ``/simsci:code-reviewer``
   → ``/simsci:pr-prep``
 - ``pr-prep`` proposes a **disposition per finding** (fix now / ticket / drop, each
   with a one-line why) at a single editable approval gate, applies only the
   approved "fix now" set as one commit per finding, re-validates with
   ``_validator``, and hands the remainder onward. It requires a clean working tree
   so each fix is independently revertible, and a fix that cannot be made green is
   reverted and becomes a ticket rather than a red PR. It stops at a **draft** PR —
   marking it ready and announcing it are separate acts it only offers
 - Extract ``_finalize-core``, the internal building block owning the finish every
   path shares: leftover-finding triage, the PR approval gate, a reviewable commit
   history, the draft PR, and the comment recording what was *not* addressed.
   ``framework-development``'s Phase 5 now delegates to it and keeps only its
   worktree teardown, so the steps that were easy to drop when improvising — the
   backlog dedup, the not-addressed comment — are structural. The step *between*
   review and finish is deliberately not shared: framework-development re-dispatches
   findings to the blind sub-agent that wrote the code, while ``pr-prep`` gates and
   edits in the main session
 - ``framework-development``: add the missing ``simsci:_split_proposer`` grant. It
   reaches ``commit-splitter`` — which groups with that agent — through
   ``_finalize-core``, and previously did so directly, so the omission has always
   caused a permission prompt mid-finalize

**0.1.0 - 07/27/26**

 - Initial release: generic developer tooling extracted from the ``simsci-internal`` plugin (MIC-7220)
 - Ships the multi-agent code review family (``/simsci:code-reviewer``), ``git-rescue``, ``commit-splitter``, ``type-hinter``, ``regression-debugger``, ``workflow-assessment``, ``change-propagation``, and ``framework-development``
