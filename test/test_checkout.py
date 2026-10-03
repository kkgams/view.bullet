"""Real Git replay of pinned checkout v5 tag-object loss (37148885612).

No HTTP, GitHub credential or fake Git subprocess. Artifact audit is separately
covered. It and canonical-origin presentation (local file transport rewrite) are
stubbed; branch/tag/object identity use real Git and the real source() call.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import publication as p

class CheckoutTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name).resolve()
  self.seed=self.base/'seed';self.seed.mkdir();self.remote=self.base/'remote.git';self.work=self.base/'work'
  self.git(self.seed,'init','-b','release');self.git(self.seed,'config','user.name','Test');self.git(self.seed,'config','user.email','test@example.invalid')
  for name in ('package.json','release.json','canonical.json'):(self.seed/name).write_bytes((ROOT/name).read_bytes())
  self.git(self.seed,'add','.');self.git(self.seed,'commit','-m','fixture')
  self.commit=self.git(self.seed,'rev-parse','HEAD');self.tag='v'+json.loads((ROOT/'package.json').read_text())['version'];self.ref='refs/tags/'+self.tag
  self.git(self.seed,'tag','-a',self.tag,'-m','fixture');self.obj=self.git(self.seed,'rev-parse',self.ref)
  subprocess.run(['git','clone','--bare',str(self.seed),str(self.remote)],check=True,capture_output=True)
  self.work.mkdir();self.git(self.work,'init')
  self.repo='kkgams/'+json.loads((ROOT/'package.json').read_text())['name'];self.git(self.work,'remote','add','origin','https://github.com/'+self.repo+'.git')
  self.git(self.work,'config','url.'+str(self.remote)+'.insteadOf','https://github.com/'+self.repo+'.git')
 def git(self,root,*args):return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.PIPE,text=True).strip()
 def history(self):self.git(self.work,'fetch','--no-tags','origin','+refs/heads/*:refs/remotes/origin/*','+refs/tags/*:refs/tags/*')
 def source(self):
  real=p.git
  def local_transport(root,*args):
   if args==('remote','get-url','origin'):
    # get-url expands local insteadOf; config retains the canonical origin.
    return self.git(root,'config','--get','remote.origin.url')
   return real(root,*args)
  with patch.dict(os.environ,{'GITHUB_SHA':self.commit,'GITHUB_REPOSITORY':self.repo}),patch.object(p.release,'check'),patch.object(p,'git',side_effect=local_transport):
   return p.source(self.work,self.work/'dist/candidate',self.tag)
 def test_legacy_checkout_replays_actual_tag_misclassification(self):
  self.history();self.assertEqual(self.git(self.work,'cat-file','-t',self.ref),'tag')
  # Exact targeted fetch from the hosted log after old testRef compares tag
  # object SHA with GITHUB_SHA (peeled commit), incorrectly considers it stale.
  self.git(self.work,'fetch','--no-tags','origin','+'+self.commit+':'+self.ref)
  self.git(self.work,'checkout','--detach',self.ref)
  self.assertEqual(self.git(self.work,'cat-file','-t',self.ref),'commit')
  with self.assertRaisesRegex(ValueError,'tag must be annotated'):self.source()
  self.assertIn(self.obj+'\t'+self.ref,self.git(self.work,'ls-remote','origin',self.ref))
 def test_event_commit_checkout_preserves_and_validates_remote_annotation(self):
  self.history();self.git(self.work,'checkout','--detach',self.commit)
  self.assertEqual(self.source(),(self.obj,self.commit))
 def test_commit_checkout_does_not_accept_lightweight_remote_tag(self):
  self.git(self.remote,'update-ref',self.ref,self.commit,self.obj)
  self.history();self.git(self.work,'checkout','--detach',self.commit)
  with self.assertRaisesRegex(ValueError,'tag must be annotated'):self.source()
 def test_both_workflow_jobs_use_event_commit_with_full_history(self):
  workflow=(ROOT/'.github/workflows/release.yml').read_text()
  checkouts=re.findall(r'- uses: actions/checkout@[^\n]+\n(.*?)(?=      - |\Z)',workflow,re.S)
  self.assertEqual(len(checkouts),2)
  for config in checkouts:
   self.assertIn('fetch-depth: 0',config)
   self.assertIn('ref: ${{ github.sha }}',config)
if __name__=='__main__':unittest.main()
