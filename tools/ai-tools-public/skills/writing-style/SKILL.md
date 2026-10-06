---
name: writing-style
description: Plain technical English for prose that people read, based on ASD-STE100 Simplified Technical English. Use it to draft a PR title or body, a PR or review comment, an issue or ticket, a commit message, a CHANGELOG entry or release note, a design document, or a written report. Workflows that post such text invoke it first.
allowed-tools: Read, Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/ste_check.py *)
---

# Writing style

Write prose for people in plain technical English. The rules below are based on
ASD-STE100 Simplified Technical English (STE), a controlled language for technical
documents. We adapted them for software work. They are not the standard, and text
that follows them is not "STE compliant".

The aim is text that a reader understands on the first read. This includes a reader
who speaks English as a second language and a reader who arrives without context.

## Scope

The rules apply to prose that a person reads. This includes PR text, ticket text,
commit message bodies, CHANGELOG entries, design documents, review replies, and
reports in the conversation.

The rules do not apply to the following:

- Code, code comments, and docstrings. The repository's code conventions govern
  those.
- Code identifiers, file paths, command-line flags, ticket keys, and product names.
- Quoted logs, quoted error text, and quoted words from a person.
- Template headings, table cells, and fixed text that another skill gives word for
  word.

## Precedence

A repository PR template or an installed team-conventions skill decides the structure
and the format. Examples are the title length, the line wrap, the required headings,
and the ticket fields. This skill governs only the sentences inside that structure.

Correct content is more important than style. Do not drop a fact, a caveat, or a
residual risk to make a sentence shorter. Use two sentences instead.

## How strict to be

Use one of three levels. The level depends on the type of text.

1. Procedures are strict. Procedures include test steps, reproduction steps, setup
   steps, and instructions to a reviewer. Write one instruction in each sentence. Use
   the imperative. Write no more than 20 words in each sentence.
2. Descriptive text in an artifact is firm. This includes a PR motivation, a ticket
   background, design-document prose, and CHANGELOG entries. Write no more than 25
   words in each sentence. Give each paragraph one topic. Write no more than six
   sentences in each paragraph.
3. Conversation replies and reports to the user follow most of the rules, but less
   strictly. Keep sentences short, use the active voice, and use the same term for
   the same thing. Do not stack hedges.

PR titles and commit subjects keep the length rules that the repository sets. Apply
only the voice and tense rules to them.

## Rules

The number after each rule is the related ASD-STE100 Issue 9 rule. The rules are in
our own words. See `references/rules.md` for the reason to keep or adapt each rule.

- Use one name for each thing and one verb for each action. When you choose a term,
  use it every time. Do not change words for variety. (1.11, 9.4)
- Do not join more than three nouns into one noun phrase. Use a preposition to break
  up a longer cluster. (2.1)
- Use simple tenses: the simple present, simple past, and simple future, and the
  imperative. Write "This PR adds", not "This PR has added". (3.2)
- Use the active voice when you know the actor. Write "`load()` calls the parser
  twice", not "The parser is called twice". (3.6)
- Use a verb for an action, not a noun made from the verb. Write "validate the
  input", not "perform validation of the input". (3.7)
- Do not use a verb form that ends in "-ing". Technical nouns such as "logging",
  "caching", and "type-hinting" are correct. Write "the loader caused", not "the
  loader was causing". (3.5)
- Do not use contractions, and do not drop articles or other short words to save
  space. (4.2, 4.5)
- Use a vertical list for text with three or more parallel items or steps. (4.3)
- Use connecting words such as "because", "therefore", and "but" to show how two
  related sentences connect. (4.4)
- Do not use semicolons. Write two sentences or a list. (8.1)
- Do not use Latin abbreviations such as "e.g.", "i.e.", and "etc.". Write "for
  example", "that is", or give the full list. (GR-6)
- Use a one-word verb instead of a verb with a particle. Write "configure", not "set
  up", and "discover", not "find out". (9.3)
- Start a warning with the condition or the command, then give the risk. Write "Do
  not force-push this branch. Other branches depend on it." (Section 7)

## Software adaptations

- Treat code identifiers, paths, flags, ticket keys, and product names as technical
  nouns. Put code identifiers and paths in backticks. Keep them exactly as written.
  The checker counts each backticked span as one word.
- Explain a specialized term the first time you use it, if the reader is not likely
  to know it. Then use it normally. Do not explain ordinary software terms.
- The STE dictionary of approved words does not apply. The restriction of each word
  to one part of speech does not apply.

## Patterns to avoid

These patterns make text sound generated instead of written.

- Sentence fragments for emphasis, and one-line paragraphs that exist to make a point
  land.
- Bold text for emphasis. Bold is correct for a label or a heading.
- The "not X, but Y" construction when the contrast does not matter.
- Metaphor and figurative words for technical things, such as "workhorse" and "under
  the hood".
- An announcement of what you will say before you say it.
- Rhetorical questions.
- A sentence that starts with "And" or "But" for effect.
- A list of three items where two items are sufficient.
- A short restatement at the end of a section.

Instead, mark a judgment as a judgment. "I would do X" and "this is a guess" are
better than a preference stated as a fact. State an uncertainty at the point where it
matters. State consequences plainly, including the bad ones.

## Check a draft before it goes out

Run the checker on each PR, ticket, or commit draft before you show it at an approval
gate, post it, or commit it. Send the draft on stdin, or give a file path:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/ste_check.py --kind markdown <<'EOF'
<draft>
EOF
```

Use `--kind wiki` for the wiki markup that some issue trackers use (`h2.` headings,
`{code}` blocks, `{{monospace}}` text). Use `--kind plain` for a commit message or an
RST file such as a CHANGELOG. Add `--skip-pattern '<regex>'` for each template line
that must stay word for word.

- Fix every hard finding. Hard findings are semicolons, contractions, Latin
  abbreviations, sentences over 35 words, and paragraphs over eight sentences.
- Read each advisory finding and fix it if it is a real problem. The passive-voice
  and "-ing" checks match patterns and do not parse grammar, so some findings are
  false. A fixed heading that a template or another skill gives is not a problem.
- Text in double quotes is exempt from all rules except the length rules. Put a
  quoted log or error message in a code block, not in quotation marks.
- Do not show the checker output at the gate unless the user asks for it. Show the
  corrected draft.

The checker does not check noun clusters, word choice, or meaning. A clean result is
necessary but not sufficient. Read the draft once more as a reader who arrives cold.

`references/examples.md` has before-and-after pairs for each type of text.

## Attribution

ASD-STE100 is a copyright and trademark of ASD, Brussels, Belgium. This skill
paraphrases some of its rules. ASD does not endorse this skill. A free copy of the standard
is available on request from https://www.asd-ste100.org.
