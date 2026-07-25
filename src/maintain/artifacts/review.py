"""Implementation review validator and finding traceability."""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from .markdown import normalized_text, require_sections
from .validation import has_transcript_contamination

SECTIONS=("## Correctness","## Requirement Compliance","## Error Handling","## Regression Risk","## Test Adequacy","## Findings","## Decision Rationale")
@dataclass(frozen=True)
class ReviewFinding:
    finding_id:str; severity:str; path:str; location:str; problem:str; why_it_matters:str; required_correction:str
@dataclass(frozen=True)
class ReviewArtifact:
    status:str; findings:tuple[ReviewFinding,...]; source_path:Path

def parse_review(path:Path, changed_paths:set[str])->ReviewArtifact:
    text=normalized_text(Path(path).read_bytes(),2_000_000)
    first=next((x.strip() for x in text.splitlines() if x.strip()),"")
    if first not in {"REVIEW_STATUS: PASS","REVIEW_STATUS: FAIL"}: raise ValueError("Invalid review status line.")
    require_sections(text,"# Implementation Review",SECTIONS)
    if has_transcript_contamination(text): raise ValueError("Transcript contamination.")
    claims=("I ran the tests","tests passed locally","I compiled","build passed")
    if any(x.casefold() in text.casefold() for x in claims): raise ValueError("Review claims unsupported local execution.")
    blocks=re.split(r"(?m)^### Finding\s+",text)[1:]; findings=[]
    for block in blocks:
        fields={}
        for key in ("ID","Severity","Path","Line or location","Problem","Why it matters","Required correction"):
            m=re.search(rf"(?mi)^-?\s*{re.escape(key)}:\s*(.+)$",block)
            if m: fields[key]=m.group(1).strip()
        if set(fields)!={"ID","Severity","Path","Line or location","Problem","Why it matters","Required correction"}: raise ValueError("Review finding is incomplete.")
        if fields["Severity"] not in {"HIGH","MEDIUM","LOW"}: raise ValueError("Invalid review severity.")
        if fields["Path"] not in changed_paths: raise ValueError("Review finding does not cite a changed path.")
        findings.append(ReviewFinding(fields["ID"],fields["Severity"],fields["Path"],fields["Line or location"],fields["Problem"],fields["Why it matters"],fields["Required correction"]))
    status=first.rsplit(" ",1)[1]
    if status=="PASS" and any(x.severity in {"HIGH","MEDIUM"} for x in findings): raise ValueError("PASS has unresolved blocking findings.")
    if status=="FAIL" and not findings: raise ValueError("FAIL requires an actionable finding.")
    return ReviewArtifact(status,tuple(findings),Path(path).resolve())

def compare_findings(previous:ReviewArtifact,current:ReviewArtifact)->dict[str,set[str]]:
    old={x.finding_id for x in previous.findings}; new={x.finding_id for x in current.findings}
    return {"resolved":old-new,"remaining":old&new,"new":new-old}
