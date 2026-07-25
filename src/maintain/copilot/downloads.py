from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import time
import zipfile
from typing import Any

ALLOWED_DOWNLOAD_EXTENSIONS = {".zip", ".md"}
DOWNLOAD_TIMEOUT_MS = 60_000
DOWNLOAD_CONTROL_SCAN_LIMIT = 20
POLL_INTERVAL_MS = 250
DOWNLOAD_OPERATION_TIMEOUT_MS = 30_000
MIN_DOWNLOAD_CLICK_TIMEOUT_MS = 5_000

# Strict/actionable selectors first. These are the only selectors clicked directly.
DIRECT_DOWNLOAD_SELECTORS = [
    'a[download]',
    'a[href$=".md" i]',
    'a[href*=".md" i]',
    'a[href$=".zip" i]',
    'a[href*=".zip" i]',
]

# Secondary controls may trigger browser downloads, but are logged distinctly.
CONTROL_DOWNLOAD_SELECTORS = [
    'button[aria-label*="Download" i]',
    '[role="button"][aria-label*="Download" i]',
    'a[aria-label*="Download" i]',
]

# Text selectors are never clicked directly. They are only used to find a nearest
# actionable ancestor. This prevents clicking visible labels that do not download.
TEXT_DOWNLOAD_SELECTORS = [
    'text=/Download/i',
    'text=/\\.md/i',
    'text=/\\.zip/i',
]

DOWNLOAD_SELECTORS = DIRECT_DOWNLOAD_SELECTORS + CONTROL_DOWNLOAD_SELECTORS + TEXT_DOWNLOAD_SELECTORS

@dataclass
class DownloadCandidate:
    locator: Any
    selector: str
    index: int
    source: str
    metadata: dict[str, str]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def validate_download_extension(extension: str) -> str:
    if extension not in ALLOWED_DOWNLOAD_EXTENSIONS:
        raise ValueError(f"Unsupported download extension: {extension}")
    return extension


def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", (name or "").strip()).strip(" .") or "copilot-output.md"


def safe_download_path(download_dir: Path, suggested_filename: str, expected_extension: str) -> Path:
    validate_download_extension(expected_extension)
    out = Path(download_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    cand = Path(sanitize_filename(suggested_filename or f"copilot-output{expected_extension}"))
    stem = cand.stem or "copilot-output"
    suffix = cand.suffix if cand.suffix.lower() == expected_extension else expected_extension
    path = out / f"{stem}{suffix}"
    idx = 1
    while path.exists():
        path = out / f"{stem}-{idx:03d}{suffix}"
        idx += 1
    return path


def validate_saved_zip(path: Path) -> None:
    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"Downloaded file was not saved: {path}")
    if path.suffix.lower() != ".zip":
        raise RuntimeError(f"Downloaded file is not a .zip file: {path}")
    if path.stat().st_size <= 0:
        raise RuntimeError(f"Downloaded file is empty: {path}")
    try:
        with zipfile.ZipFile(path, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise RuntimeError(f"Downloaded zip contains a corrupt member: {bad}")
            if not [info for info in zf.infolist() if not info.is_dir()]:
                raise RuntimeError(f"Downloaded zip has no files inside: {path}")
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"Downloaded file is not a valid zip archive: {path}") from exc


def validate_saved_markdown(path: Path) -> None:
    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"Downloaded file was not saved: {path}")
    if path.suffix.lower() != ".md":
        raise RuntimeError(f"Downloaded file is not a .md file: {path}")
    if path.stat().st_size <= 0:
        raise RuntimeError(f"Downloaded markdown file is empty: {path}")


def validate_saved_download(path: Path, expected_extension: str) -> None:
    validate_download_extension(expected_extension)
    validate_saved_markdown(path) if expected_extension == ".md" else validate_saved_zip(path)


def snapshot_download_control_counts(page: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for selector in DOWNLOAD_SELECTORS:
        try:
            counts[selector] = page.locator(selector).count()
        except Exception:
            counts[selector] = 0
    print(f"[INFO] Download baseline counts: {counts}", flush=True)
    return counts


def _candidate_metadata(locator: Any, selector: str, index: int, source: str) -> dict[str, str]:
    metadata: dict[str, str] = {"selector": selector, "index": str(index), "source": source}
    try:
        metadata["tag"] = str(locator.evaluate("el => el.tagName || ''", timeout=500) or "")
    except Exception:
        metadata["tag"] = ""
    for attr in ("role", "href", "download", "aria-label", "title"):
        try:
            metadata[attr] = normalize_text(locator.get_attribute(attr, timeout=500) or "")
        except Exception:
            metadata[attr] = ""
    try:
        metadata["text"] = normalize_text(locator.inner_text(timeout=500))[:240]
    except Exception:
        metadata["text"] = ""
    return metadata


def _metadata_key(metadata: dict[str, str]) -> str:
    return "|".join(metadata.get(k, "") for k in ("selector", "index", "tag", "href", "download", "aria-label", "title", "text"))


def _is_extension_compatible(metadata: dict[str, str], expected_extension: str) -> bool:
    if expected_extension == ".zip":
        return True
    href = metadata.get("href", "").lower()
    download = metadata.get("download", "").lower()
    text = metadata.get("text", "").lower()
    aria = metadata.get("aria-label", "").lower()
    title = metadata.get("title", "").lower()
    if ".zip" in href or download.endswith(".zip"):
        return False
    if ".md" in href or download.endswith(".md"):
        return True
    # M365 Copilot often labels a generic download button without exposing the
    # eventual filename. Allow generic buttons, but reject obvious zip controls.
    if "download" in aria or "download" in title or "download" in text:
        return ".zip" not in text and ".zip" not in aria and ".zip" not in title
    return False


def _resolve_text_candidate(text_locator: Any, expected_extension: str) -> tuple[Any | None, str]:
    ancestor_specs = [
        ("ancestor anchor with download/md", "xpath=ancestor-or-self::a[@download or contains(translate(@href, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '.md')][1]"),
        ("ancestor download button", "xpath=ancestor-or-self::button[contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'download')][1]"),
        ("ancestor role button", "xpath=ancestor-or-self::*[@role='button' and contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'download')][1]"),
    ]
    if expected_extension == ".zip":
        ancestor_specs.insert(1, ("ancestor anchor with zip", "xpath=ancestor-or-self::a[@download or contains(translate(@href, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '.zip')][1]"))
    for source, selector in ancestor_specs:
        try:
            candidate = text_locator.locator(selector).first
            if candidate.count() > 0 and candidate.is_visible(timeout=250):
                return candidate, source
        except Exception:
            continue
    return None, "unresolved text match"


def collect_download_controls(
    page: Any,
    deadline: Any,
    baseline_counts: dict[str, int] | None = None,
    expected_extension: str = ".md",
) -> list[DownloadCandidate]:
    validate_download_extension(expected_extension)
    end = time.monotonic() + deadline.bounded_timeout(DOWNLOAD_TIMEOUT_MS) / 1000
    baseline_counts = baseline_counts or {}
    seen: set[str] = set()
    last_counts: dict[str, int] = {}

    while time.monotonic() < end:
        candidates: list[DownloadCandidate] = []
        for selector in DOWNLOAD_SELECTORS:
            try:
                loc = page.locator(selector)
                count = min(loc.count(), DOWNLOAD_CONTROL_SCAN_LIMIT)
                last_counts[selector] = count
            except Exception:
                last_counts[selector] = 0
                continue

            baseline = min(max(0, baseline_counts.get(selector, 0)), count)
            if count <= baseline:
                continue

            for index in reversed(range(baseline, count)):
                raw = loc.nth(index)
                try:
                    if not raw.is_visible(timeout=250):
                        continue
                except Exception:
                    continue

                actionable = raw
                source = "direct selector"
                if selector in TEXT_DOWNLOAD_SELECTORS:
                    actionable, source = _resolve_text_candidate(raw, expected_extension)
                    if actionable is None:
                        continue

                metadata = _candidate_metadata(actionable, selector, index, source)
                if not _is_extension_compatible(metadata, expected_extension):
                    print(f"[INFO] Skipping non-{expected_extension} download candidate: {metadata}", flush=True)
                    continue

                key = _metadata_key(metadata)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(DownloadCandidate(actionable, selector, index, source, metadata))
                print(f"[INFO] Download candidate accepted: {metadata}", flush=True)
                if len(candidates) >= DOWNLOAD_CONTROL_SCAN_LIMIT:
                    return candidates

        if candidates:
            print(f"[INFO] Download controls found: {len(candidates)}", flush=True)
            return candidates
        page.wait_for_timeout(POLL_INTERVAL_MS)

    print(f"[WARN] No matching new actionable download controls. Final counts: {last_counts}; baseline: {baseline_counts}", flush=True)
    return []


def download_file_after_baseline(
    page: Any,
    download_dir: Path,
    expected_extension: str,
    deadline: Any,
    baseline_counts: dict[str, int],
) -> Path:
    controls = collect_download_controls(page, deadline, baseline_counts, expected_extension)
    return _download_from_controls(page, download_dir, expected_extension, controls, stale_filtered=True)


def download_file_if_expected(page: Any, download_dir: Path, expected_extension: str, deadline: Any) -> Path:
    controls = collect_download_controls(page, deadline, None, expected_extension)
    return _download_from_controls(page, download_dir, expected_extension, controls, stale_filtered=False)


def _download_from_controls(
    page: Any,
    download_dir: Path,
    expected_extension: str,
    controls: list[DownloadCandidate],
    stale_filtered: bool,
) -> Path:
    validate_download_extension(expected_extension)
    if not controls:
        qualifier = "new actionable " if stale_filtered else "actionable "
        raise RuntimeError(f"Expected {qualifier}{expected_extension} download, but no downloadable control was found.")

    last_error: Exception | None = None
    attempted_metadata: list[dict[str, str]] = []
    for attempt, candidate in enumerate(controls, start=1):
        save_path: Path | None = None
        attempted_metadata.append(candidate.metadata)
        try:
            print(f"[INFO] Attempting download control {attempt}/{len(controls)}: {candidate.metadata}", flush=True)
            with page.expect_download(timeout=DOWNLOAD_OPERATION_TIMEOUT_MS) as info:
                candidate.locator.click(timeout=max(MIN_DOWNLOAD_CLICK_TIMEOUT_MS, DOWNLOAD_OPERATION_TIMEOUT_MS))
            download = info.value
            failure = download.failure()
            if failure:
                raise RuntimeError(f"Browser reported download failure: {failure}")
            save_path = safe_download_path(
                download_dir,
                download.suggested_filename or f"copilot-output{expected_extension}",
                expected_extension,
            )
            download.save_as(str(save_path))
            validate_saved_download(save_path, expected_extension)
            print(f"[INFO] Download saved: {save_path}", flush=True)
            return save_path
        except Exception as exc:
            last_error = exc
            print(f"[WARN] Download control {attempt} failed: {exc}", flush=True)
            if save_path and save_path.exists():
                try:
                    save_path.unlink()
                except OSError:
                    pass

    raise RuntimeError(
        f"No usable non-empty {expected_extension} download was found. "
        f"Last error: {last_error}. Attempted candidates: {attempted_metadata}"
    )
