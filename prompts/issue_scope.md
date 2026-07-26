# Task
Read the issue report, repository map, and every supplied focused file. Define the smallest evidence-grounded tasks needed to diagnose and correct the issue.

# Output contract
Follow `TASK.md` exactly. For the browser JSON contract, return only one JSON envelope with `content.tasks` in dependency order. Every task must include `id`, `objective`, `allowed_files`, `done_when`, `verification`, and `depends_on`. Use exact supplied paths for existing files. When `project_policy.allow_new_files` is true and the correction requires a new file, choose a conventional minimal repository-relative path that directly matches the issue instead of asking the user to name it. If essential existing code is absent, return `content.context_queries` instead of guessing.
