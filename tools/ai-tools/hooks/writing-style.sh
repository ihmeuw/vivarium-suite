#!/usr/bin/env bash
# Add the SimSci writing-style text to the session context.
# Set SIMSCI_WRITING_STYLE=off to disable it.
if [ "${SIMSCI_WRITING_STYLE:-on}" = "off" ]; then
    exit 0
fi
# Escape backslashes and double quotes and join the lines, so the text is a valid JSON string.
text=$(sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' "${CLAUDE_PLUGIN_ROOT}/hooks/writing-style.txt" | tr '\n' ' ')
printf '{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "%s"}}\n' "$text"
