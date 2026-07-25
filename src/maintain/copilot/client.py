"""PoC-derived, artifact-first Microsoft 365 Copilot client."""
from __future__ import annotations
import time, zipfile
from pathlib import Path
from typing import Protocol
try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:
    def sync_playwright(): raise RuntimeError("Playwright is not installed. Install Maintain with the browser extra.")
from .browser import (GlobalDeadline, attach_file_to_chat, get_or_create_page, launch_context,
                      normalize_url, start_fresh_chat, submit_prompt, wait_for_chat_ready,
                      wait_for_response_complete)
from .downloads import (download_file_after_baseline, snapshot_download_control_counts,
                        validate_saved_markdown, validate_saved_zip)
from .models import select_copilot_model
from .result import ArtifactResult, ARTIFACT_RECEIVED, SUBMISSION_CONFIRMED

class CopilotArtifactClient(Protocol):
    def create_markdown_artifact(self,*,prompt:str,exchange_package:Path,output_filename:str,transient_dir:Path)->ArtifactResult: ...
    def create_zip_artifact(self,*,prompt:str,exchange_package:Path,output_filename:str,transient_dir:Path)->ArtifactResult: ...

class BrowserCopilotClient:
    def __init__(self,*,url:str,model:str,profile_dir:Path,browser_channel:str="msedge",browser_session_retries:int=3,navigation_retries:int=3):
        self.url=url; self.model=model; self.profile_dir=Path(profile_dir); self.browser_channel=browser_channel
        self.browser_session_retries=browser_session_retries; self.navigation_retries=navigation_retries
    def _create(self,prompt: str, package:Path, filename:str, transient:Path, extension:str)->ArtifactResult:
        if Path(filename).name!=filename or not filename.endswith(extension): raise ValueError("Output filename is unsafe or has the wrong extension.")
        package=Path(package).resolve()
        if package.suffix.lower() != ".zip": raise ValueError("Each request must attach one ZIP exchange package.")
        last=None
        for session_attempt in range(1,self.browser_session_retries+1):
            submitted=False
            try:
                with sync_playwright() as pw:
                    ctx=launch_context(pw,self.profile_dir,self.browser_channel)
                    try:
                        page=get_or_create_page(ctx); deadline=GlobalDeadline(time.monotonic(),300_000)
                        nav=0
                        for nav in range(1,self.navigation_retries+1):
                            try: page.goto(normalize_url(self.url),wait_until="domcontentloaded",timeout=deadline.bounded_timeout(45_000)); break
                            except Exception:
                                if nav==self.navigation_retries: raise
                        session=start_fresh_chat(page,deadline)
                        select_copilot_model(page,self.model,deadline)
                        box=wait_for_chat_ready(page,deadline); attach_file_to_chat(page,package,deadline)
                        box=wait_for_chat_ready(page,deadline); baseline=snapshot_download_control_counts(page)
                        submit_prompt(page,box,prompt,deadline); submitted=True
                        wait_for_response_complete(page,deadline)
                        saved=download_file_after_baseline(page,transient,extension,GlobalDeadline(time.monotonic(),120_000),baseline)
                        target=Path(transient).resolve()/filename; target.parent.mkdir(parents=True,exist_ok=True)
                        if saved.resolve()!=target:
                            if target.exists(): raise FileExistsError(target)
                            saved.rename(target)
                        (validate_saved_markdown if extension==".md" else validate_saved_zip)(target)
                        return ArtifactResult(target,"markdown" if extension==".md" else "zip",filename,saved.name,False,ARTIFACT_RECEIVED,session_attempt,nav,1,1,())
                    finally: ctx.close()
            except Exception as exc:
                last=exc
                if submitted: raise RuntimeError(f"Submission confirmed but output was not recovered: {exc}") from exc
                if session_attempt==self.browser_session_retries: raise
        raise RuntimeError(str(last))
    def create_markdown_artifact(self,*,prompt:str,exchange_package:Path,output_filename:str,transient_dir:Path)->ArtifactResult:
        return self._create(prompt,exchange_package,output_filename,transient_dir,".md")
    def create_zip_artifact(self,*,prompt:str,exchange_package:Path,output_filename:str,transient_dir:Path)->ArtifactResult:
        return self._create(prompt,exchange_package,output_filename,transient_dir,".zip")
