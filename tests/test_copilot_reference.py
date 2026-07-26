from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from maintain.errors import ConfigurationError, ProviderError
from maintain.exchange_package import build_exchange_package
from maintain.models import ProviderRequest
from maintain.providers.browser import BrowserProvider
from maintain.references import (
    CopilotReference,
    prepare_reference,
    reference_submission_line,
    validate_reference,
    verify_reference,
)


def _request() -> ProviderRequest:
    return ProviderRequest(
        schema_version=1,
        run_id="run-reference",
        task_id="scope-reference",
        role="scope",
        instructions="Plan the requested change.",
        payload={
            "candidate_files": [{
                "path": "src/example.py",
                "content": "VALUE = 1\n",
            }],
        },
    )


def test_package_without_reference_preserves_the_three_file_format(
        tmp_path: Path) -> None:
    implicit = build_exchange_package(_request(), tmp_path / "implicit")
    explicit = build_exchange_package(
        _request(), tmp_path / "explicit", reference_path=None)

    assert [path.name for path in implicit.paths] == [
        "TASK.md", "CODEBASE.md", "MANIFEST.json"]
    assert [path.name for path in explicit.paths] == [
        "TASK.md", "CODEBASE.md", "MANIFEST.json"]
    assert [path.read_bytes() for path in implicit.paths] == [
        path.read_bytes() for path in explicit.paths]
    assert implicit.sha256 == explicit.sha256
    manifest = json.loads(implicit.paths[2].read_text(encoding="utf-8"))
    assert [item["purpose"] for item in manifest["attachments"]] == [
        "task", "focused_codebase"]


def test_local_reference_is_snapshotted_and_attached_as_the_fourth_file(
        tmp_path: Path) -> None:
    source = tmp_path / "product brief.pdf"
    contents = b"%PDF-1.7\nuser supplied requirements\n"
    source.write_bytes(contents)
    reference = prepare_reference(source, tmp_path / "snapshots")
    source.write_bytes(b"changed after snapshot")

    package = build_exchange_package(
        _request(), tmp_path / "package", reference=reference)

    assert reference == CopilotReference(
        kind="file",
        source=str(source.resolve()),
        name="product brief.pdf",
        path=(tmp_path / "snapshots" / "product brief.pdf").resolve(),
        bytes=len(contents),
        sha256=hashlib.sha256(contents).hexdigest(),
    )
    assert [path.name for path in package.paths] == [
        "TASK.md", "CODEBASE.md", "MANIFEST.json", "product brief.pdf"]
    assert package.paths[3] == reference.path
    assert package.paths[3].read_bytes() == contents

    manifest = json.loads(package.paths[2].read_text(encoding="utf-8"))
    record = next(
        item for item in manifest["attachments"]
        if item["purpose"] == "user_reference")
    assert record == {
        "name": "product brief.pdf",
        "purpose": "user_reference",
        "bytes": len(contents),
        "sha256": hashlib.sha256(contents).hexdigest(),
    }
    task = package.paths[0].read_text(encoding="utf-8")
    assert "`product brief.pdf` is read-only user-supplied background material" in task
    assert "do not treat it as repository code" in task


def test_reference_validation_rejects_missing_invalid_and_oversized_input(
        tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="does not exist"):
        validate_reference(tmp_path / "missing.txt")
    with pytest.raises(ConfigurationError, match="complete HTTPS URL"):
        validate_reference("http://example.com/reference")

    oversized = tmp_path / "large.log"
    oversized.write_bytes(b"12345")
    with pytest.raises(ConfigurationError, match="too large"):
        prepare_reference(oversized, tmp_path / "snapshots", max_bytes=4)


@pytest.mark.parametrize("name", ["TASK.md", "codebase.MD", "Manifest.Json"])
def test_reference_filename_cannot_collide_with_package_files(
        tmp_path: Path, name: str) -> None:
    source = tmp_path / name
    source.write_text("reference", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="filename is reserved"):
        prepare_reference(source, tmp_path / "snapshots")
    with pytest.raises(ConfigurationError, match="filename is reserved"):
        build_exchange_package(_request(), tmp_path / "package", source)


def test_url_reference_has_an_explicit_non_fetching_submission_line(
        tmp_path: Path) -> None:
    url = "https://contoso.sharepoint.com/sites/Product/Shared%20Documents/spec.docx"
    reference = prepare_reference(url, tmp_path / "unused")

    assert reference == CopilotReference(
        kind="url",
        source=url,
        name="contoso.sharepoint.com",
        path=None,
        bytes=None,
        sha256=None,
    )
    line = reference_submission_line(reference)
    assert url in line
    assert "read-only reference URL" in line
    assert "Maintain did not open or verify" in line
    assert "unless you actually did" in line
    assert not (tmp_path / "unused").exists()

    package = build_exchange_package(
        _request(), tmp_path / "package", reference=reference)
    assert [path.name for path in package.paths] == [
        "TASK.md", "CODEBASE.md", "MANIFEST.json"]
    task = package.paths[0].read_text(encoding="utf-8")
    assert url in task
    assert "Maintain did not open or verify its content" in task
    manifest = json.loads(package.paths[2].read_text(encoding="utf-8"))
    assert manifest["user_reference"] == {
        "kind": "url",
        "purpose": "user_reference",
        "url": url,
        "opened_by_maintain": False,
    }


def test_url_reference_length_is_bounded(tmp_path: Path) -> None:
    oversized_url = "https://example.com/" + ("a" * 4096)

    with pytest.raises(ConfigurationError, match="cannot exceed"):
        prepare_reference(oversized_url, tmp_path / "unused")


def test_browser_provider_accepts_only_an_unchanged_prepared_reference(
        tmp_path: Path) -> None:
    source = tmp_path / "requirements.md"
    source.write_text("Initial requirements", encoding="utf-8")
    reference = prepare_reference(source, tmp_path / "snapshots")
    provider = BrowserProvider(
        "m365_copilot_browser",
        {
            "url": "https://m365.cloud.microsoft/chat",
            "profile_dir": str(tmp_path / "profile"),
        },
        tmp_path / "evidence",
    )

    provider.set_reference_material(reference)
    verify_reference(reference)

    assert provider._reference_material == reference
    assert reference.path is not None
    reference.path.write_text("tampered", encoding="utf-8")
    provider.set_reference_material(reference)
    with pytest.raises(
            ProviderError,
            match="prepared Copilot reference is unavailable"):
        provider._verify_reference_material(reference)


def test_saved_file_reference_round_trips_with_a_run_relative_path(
        tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    source = tmp_path / "acceptance.txt"
    source.write_text("Must retain recent projects.", encoding="utf-8")
    reference = prepare_reference(
        source, run_dir / "artifacts" / "references")

    saved = reference.to_dict(run_dir=run_dir)
    source.unlink()
    loaded = CopilotReference.from_dict(saved, run_dir=run_dir)

    assert saved["path"] == "artifacts/references/acceptance.txt"
    assert loaded == reference


def test_saved_url_reference_round_trips_without_a_path(tmp_path: Path) -> None:
    reference = prepare_reference(
        "https://contoso.sharepoint.com/sites/Product/spec",
        tmp_path / "unused",
    )

    assert CopilotReference.from_dict(reference.to_dict()) == reference


def test_saved_reference_loader_rejects_path_escape_and_ambiguous_paths(
        tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    metadata = {
        "kind": "file",
        "source": str(outside),
        "name": outside.name,
        "path": "../outside.txt",
        "bytes": outside.stat().st_size,
        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
    }

    with pytest.raises(ConfigurationError, match="escapes its run directory"):
        CopilotReference.from_dict(metadata, run_dir=run_dir)

    metadata["path"] = str(outside.resolve())
    with pytest.raises(ConfigurationError, match="must be relative"):
        CopilotReference.from_dict(metadata, run_dir=run_dir)

    metadata["path"] = "artifacts/reference.txt"
    with pytest.raises(ConfigurationError, match="needs its run directory"):
        CopilotReference.from_dict(metadata)


def test_saved_reference_loader_rejects_unknown_fields_and_tampering(
        tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    source = tmp_path / "brief.md"
    source.write_text("Original brief", encoding="utf-8")
    reference = prepare_reference(source, run_dir / "artifacts")
    saved = reference.to_dict(run_dir=run_dir)

    with pytest.raises(ConfigurationError, match="unexpected or missing"):
        CopilotReference.from_dict({**saved, "extra": True}, run_dir=run_dir)

    saved["sha256"] = "0" * 64
    with pytest.raises(ConfigurationError, match="changed after it was prepared"):
        CopilotReference.from_dict(saved, run_dir=run_dir)


def test_reference_metadata_cannot_be_made_relative_to_another_run(
        tmp_path: Path) -> None:
    source = tmp_path / "brief.md"
    source.write_text("Brief", encoding="utf-8")
    reference = prepare_reference(source, tmp_path / "run-a" / "artifacts")

    with pytest.raises(ConfigurationError, match="outside the run directory"):
        reference.to_dict(run_dir=tmp_path / "run-b")
