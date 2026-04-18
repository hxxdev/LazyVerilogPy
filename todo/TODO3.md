### Enforce single-line formatting for include

Requirement

Each include directive must:
Occupy exactly one line
Not be split across lines
Be separated cleanly from surrounding code

Expected formatting

`include "foo.svh"

Action items

In _break_decision():
Force line break before and after include
Prevent wrapping or indentation anomalies

After finishing this job, make a commit using `git commit -m <your message>`.
