# Rule table

This table lists the ASD-STE100 rules that the `writing-style` skill uses and the
reason for each decision. The rule text is in our own words. The table does not copy
the standard or its dictionary.

The rule numbers come from ASD-STE100 Issue 9 (January 2025). We took the numbers
from a third-party summary and did not check them against the standard. The table
gives the Section 6 limits for descriptive text. It does not give the Section 6 rule
numbers because we could not confirm them.

| Rule | What it asks | Decision | Reason |
| --- | --- | --- | --- |
| 1.1 | Use only approved dictionary words, technical nouns, and technical verbs. | Drop | The dictionary has no words for merge, rebase, cache, or endpoint. We treat code identifiers and software terms as technical nouns instead. |
| 1.2, 1.3 | Use a word only as its approved part of speech and with its approved meaning. | Drop | These rules depend on the dictionary, and they conflict with normal developer English. |
| 1.11, 9.4 | Use one term for one thing, and keep terms consistent. | Keep | A reader who sees two names assumes two things. This rule gives the most value for software text. |
| 2.1 | Do not join more than three nouns into a noun phrase. | Keep | Long noun clusters such as "model run output file path" are hard to parse. The checker cannot detect them, so the writer must. |
| 3.2 | Use only the simple tenses, the imperative, and the past participle as an adjective. | Keep | It removes "has been added" and similar forms. PR text reads better in the simple present. |
| 3.4 | Do not use auxiliary verbs to make complex verb forms. | Keep | It follows from rule 3.2. |
| 3.5 | Use the "-ing" form only in a technical noun. | Adapt | Software has many "-ing" nouns, such as logging and caching. We forbid only the verb use. |
| 3.6 | Use the active voice. | Keep | The passive voice hides the actor, and in a bug report the actor is usually the point. |
| 3.7 | Use a verb for an action, not a noun. | Keep | "Validate the input" is shorter and clearer than "perform validation of the input". |
| 4.1 | Write short, clear sentences. | Keep | The limits come from Sections 5 and 6. |
| 4.2 | Do not omit words or use contractions. | Keep | Dropped words make text ambiguous for second-language readers. |
| 4.3 | Use a vertical list for complex text. | Keep | Markdown and wiki markup both render lists. |
| 4.4 | Use connecting words between related sentences. | Keep | Short sentences without connecting words read as a list of unrelated facts. |
| 4.5 | Use an article or a demonstrative before a noun when applicable. | Keep | It prevents telegraphic text such as "Fix bug in loader". Titles and commit subjects are exempt. |
| 5.1 | Write no more than 20 words in a procedural sentence. | Keep | Procedures are test steps, reproduction steps, and reviewer instructions. |
| 5.2, 5.3 | Write one instruction per sentence, in the imperative. | Keep | A reader who follows steps must see where one step ends. |
| Section 6 | Write no more than 25 words in a descriptive sentence, one topic per paragraph, and no more than six sentences per paragraph. | Keep | These limits apply to PR, ticket, and design-document prose. |
| Section 7 | Start a warning with the condition or the command. | Keep | It applies to warnings about data loss and destructive commands. |
| 8.1 | Do not use semicolons. | Keep | The checker can detect semicolons with no false positives outside code. |
| 8.5, 8.7 | Count parenthetical text and hyphenated words as one word. | Keep | The checker also counts each backticked span as one word. That is our convention. |
| 9.3 | Do not use phrasal verbs. | Keep | Phrasal verbs are hard for second-language readers. The checker uses a short list that we wrote. |
| GR-6 | Do not use Latin abbreviations. | Keep | "e.g." and "i.e." are often confused. |

## Rules from outside the standard

The skill adds a list of patterns to avoid. These patterns come from the experience of
our team with generated text, not from ASD-STE100. Examples are bold text for
emphasis, figurative language, rhetorical questions, and a restatement at the end of
a section.
