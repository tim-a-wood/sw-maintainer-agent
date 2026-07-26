# Task
Independently review the supplied task, completion criteria, complete changed files, and actual diff. Cite only supplied evidence and do not claim local execution.

# Output contract
Follow `TASK.md` exactly. For the browser JSON contract, return only one JSON envelope with `content.decision` equal to `approve` or `changes_requested` and `content.findings` as a list. Every finding must include `severity`, `file`, `line`, `evidence`, and `remediation`; return an empty list when approved.
