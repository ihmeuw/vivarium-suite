#!/usr/bin/env bash
# Add the SimSci writing-style summary to the session context.
# Set SIMSCI_WRITING_STYLE=off to disable it.
if [ "${SIMSCI_WRITING_STYLE:-on}" = "off" ]; then
    exit 0
fi
# The text states facts, not commands, so that it reads as project information.
cat <<'JSON'
{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "SimSci writes prose for people in plain technical English, based on ASD-STE100 Simplified Technical English. Replies to the user use short sentences, the active voice, simple tenses, and one name for each thing. They have no semicolons, no contractions, and no Latin abbreviations. Code, code comments, docstrings, identifiers, and quoted text keep their own conventions. PR text, ticket text, commit messages, CHANGELOG entries, and design documents follow the full rules in the simsci:writing-style skill. The SimSci ticket and PR workflows run its checker on ticket text and PR text before they show or post it."}}
JSON
