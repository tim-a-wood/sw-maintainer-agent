# Task
Read `TASK.md` first, then use only the attached focused code, manifest, and approved issue analysis. Implement the correction within the authorized paths.

# Output contract
The output contract in `TASK.md` is authoritative. For the browser JSON contract, return only one complete JSON envelope: `content.files` contains `path` and complete final string `content` for every added or modified file; `content.deleted_files` contains deletions; and `content.changed_files` is exactly their union. Include a code-grounded `content.root_cause` with `statement` and `evidence_paths`. Do not return a patch, excerpt, placeholder, Markdown wrapper, or downloadable artifact unless `TASK.md` explicitly requires that transport.
