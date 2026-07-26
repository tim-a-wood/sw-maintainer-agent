# Task
Read `TASK.md` first, then use only the attached focused code, manifest, and approved issue analysis. Implement the correction within the authorized paths.

# Output contract
The output contract in `TASK.md` is authoritative. For the browser contract, use the transport that `TASK.md` explicitly requires. An inline task puts complete final files in `content.files`. A ZIP task attaches `maintain-output.zip`, stores files at their exact authorized repository-relative paths, and keeps `content.files` empty. In both cases, `content.deleted_files` contains deletions and `content.changed_files` is exactly the union of added, modified, and deleted paths. Include a code-grounded `content.root_cause` with `statement` and `evidence_paths`. Do not return a patch, excerpt, placeholder, or Markdown wrapper.
