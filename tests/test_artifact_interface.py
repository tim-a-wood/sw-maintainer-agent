from __future__ import annotations
import json, tempfile, unittest, zipfile
from pathlib import Path
from maintain.artifacts.implementation import parse_markdown_implementation, parse_zip_implementation
from maintain.artifacts.markdown import headings_outside_fences
from maintain.artifacts.review import parse_review, compare_findings
from maintain.artifacts.validation import validate_repository_path
from maintain.copilot.package import create_exchange_package, validate_exchange_package
from maintain.workflows.state import Checkpoint, next_resume_action
from maintain.verification.models import VerificationCommand
from maintain.verification.runner import run_command

class ArtifactTests(unittest.TestCase):
 def setUp(self): self.t=tempfile.TemporaryDirectory(); self.root=Path(self.t.name)
 def tearDown(self): self.t.cleanup()
 def md(self,body):
  p=self.root/'i.md'; p.write_text(body,encoding='utf-8'); return p
 def test_valid_add(self):
  p=self.md('# Implementation Artifact\n## Summary\nS\n## File Operations\n### Add: main.c\n```c\nint main(void){return 0;}\n```\n')
  a=parse_markdown_implementation(p,self.root/'s',authorized={('add','main.c')}); self.assertEqual(a.operations[0].path,'main.c')
  self.assertEqual(a.operations[0].staged_content.read_text(),'int main(void){return 0;}')
 def test_valid_modify_delete(self):
  p=self.md('# Implementation Artifact\n## Summary\nS\n## File Operations\n### Modify: a.py\n```python\nx=1\n```\n### Delete: old.py\nReason:\nOld\n')
  a=parse_markdown_implementation(p,self.root/'s'); self.assertEqual([x.operation for x in a.operations],['modify','delete'])
 def test_duplicate_path(self):
  p=self.md('# Implementation Artifact\n## Summary\nS\n## File Operations\n### Add: a.py\n```\nx\n```\n### Modify: a.py\n```\ny\n```\n')
  with self.assertRaisesRegex(ValueError,'Duplicate'): parse_markdown_implementation(p,self.root/'s')
 def test_delete_content_rejected(self):
  p=self.md('# Implementation Artifact\n## Summary\nS\n## File Operations\n### Delete: a.py\n```\nx\n```\n')
  with self.assertRaisesRegex(ValueError,'Delete'): parse_markdown_implementation(p,self.root/'s')
 def test_heading_in_fence_ignored(self):
  text='# X\n```\n## Fake\n```\n## Real\n'; self.assertEqual([h for _,h in headings_outside_fences(text)],['# X','## Real'])
 def test_unsafe_paths(self):
  for p in ('../x','/x','C:/x','a\\b'):
   with self.subTest(p=p), self.assertRaises(ValueError): validate_repository_path(p)
 def test_bom_and_crlf(self):
  p=self.root/'i.md'; p.write_bytes(b'\xef\xbb\xbf# Implementation Artifact\r\n## Summary\r\nS\r\n## File Operations\r\n### Add: a\r\n```\r\nx\r\n```\r\n')
  self.assertEqual(parse_markdown_implementation(p,self.root/'s').operations[0].path,'a')
 def test_zip_valid(self):
  p=self.root/'i.zip'; d={'schema_version':1,'operations':[{'operation':'add','path':'main.c','content_member':'files/main.c'}],'implementation_summary':'S'}
  with zipfile.ZipFile(p,'w') as z: z.writestr('IMPLEMENTATION.json',json.dumps(d)); z.writestr('files/main.c','x')
  a=parse_zip_implementation(p,self.root/'s'); self.assertEqual(a.operations[0].staged_content.read_text(),'x')
 def test_zip_undeclared_rejected(self):
  p=self.root/'i.zip'; d={'schema_version':1,'operations':[],'implementation_summary':'S'}
  with zipfile.ZipFile(p,'w') as z: z.writestr('IMPLEMENTATION.json',json.dumps(d)); z.writestr('extra','x')
  with self.assertRaisesRegex(ValueError,'Undeclared'): parse_zip_implementation(p,self.root/'s')
 def test_package_round_trip(self):
  p=create_exchange_package(self.root/'x.zip',operation_id='x',operation_type='feature',base_tree_hash='abc',expected_output_filename='i.md',members={'TASK.md':'task','INPUTS/REQUEST.md':'request'})
  self.assertEqual(validate_exchange_package(p)['operation_id'],'x')
 def test_resume_mapping(self): self.assertEqual(next_resume_action(Checkpoint.ARTIFACT_RECEIVED),'validate_existing')
 def test_verification(self):
  r=run_command(VerificationCommand('x',True,(__import__('sys').executable,'-c','print("ok")')),self.root)
  self.assertTrue(r.passed); self.assertEqual(r.stdout.strip(),'ok')
 def review(self,status='FAIL',severity='HIGH',fid='F-1'):
  text=f'''REVIEW_STATUS: {status}\n# Implementation Review\n## Correctness\nA\n## Requirement Compliance\nA\n## Error Handling\nA\n## Regression Risk\nA\n## Test Adequacy\nA\n## Findings\n'''
  if status=='FAIL' or severity: text+=f'''### Finding 1\n- ID: {fid}\n- Severity: {severity}\n- Path: a.py\n- Line or location: 1\n- Problem: P\n- Why it matters: W\n- Required correction: C\n'''
  return text+'## Decision Rationale\nR\n'
 def test_review_fail(self):
  p=self.md(self.review()); self.assertEqual(parse_review(p,{'a.py'}).status,'FAIL')
 def test_review_pass_with_high_rejected(self):
  p=self.md(self.review('PASS','HIGH')); self.assertRaises(ValueError,parse_review,p,{'a.py'})
