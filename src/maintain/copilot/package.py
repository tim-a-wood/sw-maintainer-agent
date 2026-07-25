"""Deterministic, one-file Copilot exchange packages."""
from __future__ import annotations
import hashlib, json, stat, zipfile
from pathlib import Path, PurePosixPath
from ..security import assert_no_secrets

ROLE_BY_MEMBER = {"TASK.md":"task", "INPUTS/REQUEST.md":"request", "INPUTS/REPOSITORY_MAP.md":"repository_map",
 "INPUTS/CODEBASE.md":"codebase", "INPUTS/CURRENT_SCOPE.md":"current_scope", "INPUTS/ISSUE_ANALYSIS.md":"issue_analysis",
 "INPUTS/CURRENT_REVIEW.md":"current_review", "INPUTS/VALIDATION_FINDINGS.json":"validation_findings",
 "INPUTS/VERIFICATION_RESULTS.json":"verification_results"}

def create_exchange_package(destination: Path, *, operation_id: str, operation_type: str,
                            base_tree_hash: str, expected_output_filename: str,
                            members: dict[str, bytes | str], max_members: int = 20,
                            max_member_bytes: int = 2_000_000, max_total_bytes: int = 10_000_000) -> Path:
    if "TASK.md" not in members: raise ValueError("TASK.md is required.")
    if len(members) > max_members: raise ValueError("Exchange package member limit exceeded.")
    records=[]; encoded={}; roles=set(); total=0
    for name, value in members.items():
        p=PurePosixPath(name)
        if p.is_absolute() or "\\" in name or ".." in p.parts or name not in ROLE_BY_MEMBER:
            raise ValueError(f"Unsupported or unsafe package member: {name}")
        role=ROLE_BY_MEMBER[name]
        if role in roles: raise ValueError(f"Duplicate semantic role: {role}")
        roles.add(role); data=value.encode() if isinstance(value,str) else bytes(value)
        if name.endswith((".md",".json")): data.decode("utf-8")
        if len(data)>max_member_bytes: raise ValueError(f"Package member too large: {name}")
        total += len(data)
        if total>max_total_bytes: raise ValueError("Exchange package limit exceeded.")
        assert_no_secrets(data.decode("utf-8", errors="ignore"), f"exchange member {name}")
        encoded[name]=data; records.append({"path":name,"semantic_role":role,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()})
    manifest={"schema_version":1,"operation_id":operation_id,"operation_type":operation_type,
              "base_tree_hash":base_tree_hash,"expected_output_filename":expected_output_filename,"members":records}
    target=Path(destination).resolve(); target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists(): raise FileExistsError(f"Refusing to overwrite package: {target}")
    with zipfile.ZipFile(target,"w",zipfile.ZIP_DEFLATED) as z:
        for name,data in encoded.items(): z.writestr(name,data)
        z.writestr("MANIFEST.json",json.dumps(manifest,sort_keys=True,indent=2).encode())
    return target

def validate_exchange_package(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        infos=z.infolist(); names=[i.filename for i in infos if not i.is_dir()]
        if len(names)!=len(set(names)): raise ValueError("Duplicate package member.")
        for i in infos:
            p=PurePosixPath(i.filename)
            if p.is_absolute() or ".." in p.parts or stat.S_ISLNK(i.external_attr>>16): raise ValueError("Unsafe package member.")
        if "MANIFEST.json" not in names: raise ValueError("Package manifest is missing.")
        m=json.loads(z.read("MANIFEST.json")); declared={x["path"] for x in m.get("members",[])}
        if set(names)!=(declared|{"MANIFEST.json"}): raise ValueError("Package inventory mismatch.")
        roles=[x["semantic_role"] for x in m["members"]]
        if len(roles)!=len(set(roles)): raise ValueError("Duplicate semantic role.")
        for x in m["members"]:
            data=z.read(x["path"])
            if len(data)!=x["bytes"] or hashlib.sha256(data).hexdigest()!=x["sha256"]: raise ValueError(f"Package hash mismatch: {x['path']}")
        return m
