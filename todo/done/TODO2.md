### TODO2: Correct token classification for include directives

Current state

include directives are classified as FTT.string_literal

Issue

This causes incorrect formatting behavior (spacing, line breaks)

Expected behavior

Treat include as a distinct directive-level token

Action items

Introduce new token type:

`FTT.include_directive`
Update _classify() to return this type
Update spacing and break rules to handle it explicitly

After finishing this job, make a commit using `git commit -m <your message>`.
