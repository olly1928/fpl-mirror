"""Bounded normal Git push of only generated latest results; never force push."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


def command(repo,*args,check=True):
    p=subprocess.run(['git',*args],cwd=repo,text=True,capture_output=True)
    if check and p.returncode:raise RuntimeError('Git '+args[0]+' failed: '+p.stderr.strip())
    return p


def validate_publication(root):
    receipt=json.loads((root/'latest/publication.json').read_text());manifest=root/'manifest.json'
    sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    if receipt.get('complete')is not True or sha(manifest)!=receipt['package_manifest_sha256']:raise ValueError('Package changed after forecast')
    package=json.loads(manifest.read_text())
    if package.get('release_validation_complete')is not True:raise ValueError('Package release validation is incomplete')
    origin=datetime.fromisoformat(receipt['origin_utc'].replace('Z','+00:00'));deadline=datetime.fromisoformat(receipt['target_deadline_utc'].replace('Z','+00:00'))
    clock=datetime.now(timezone.utc)
    if origin.tzinfo is None or deadline.tzinfo is None or not 0<=(clock-origin).total_seconds()<=7200 or (deadline-clock).total_seconds()<300:
        raise ValueError('Forecast is stale or too close to its target deadline')
    for name,h in receipt['files'].items():
        path=(root/'latest'/name).resolve()
        if path.parent!=root/'latest'or sha(path)!=h:raise ValueError('Published forecast file changed')
    for name,h in package['code_files'].items():
        path=(root/name).resolve()
        if root not in path.parents or sha(path)!=h:raise ValueError('Runtime source changed after forecast')
    return receipt


def commit_latest(repo,model_directory,branch):
    repo=Path(repo).resolve()
    if not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*',model_directory):raise ValueError('Expected a plain relative package directory')
    root=(repo/model_directory).resolve()
    if root!=repo/model_directory:raise ValueError('Package symlinks are unsupported')
    command(repo,'check-ref-format','refs/heads/'+branch)
    base=command(repo,'rev-parse','HEAD').stdout.strip();prefix=model_directory+'/latest/'
    validate_publication(root)
    command(repo,'add','--',model_directory+'/latest')
    changed=command(repo,'diff','--cached','--name-only').stdout.splitlines()
    if any(not x.startswith(prefix)for x in changed):raise ValueError('Only generated latest files may be staged')
    if not changed:return {'complete':True,'commit':base,'changed':False,'push_attempts':0}
    if command(repo,'diff','--quiet',check=False).returncode:raise ValueError('Unexpected uncommitted tracked changes')
    command(repo,'commit','-m','Update on-demand fixed-model FPL forecasts')
    publication_parent=base
    for attempt in range(1,4):
        command(repo,'fetch','--no-tags','origin',branch)
        remote=command(repo,'rev-parse','FETCH_HEAD').stdout.strip()
        # The existing collector may change files elsewhere. Any change to this
        # reviewed package (apart from generated latest output) fails closed.
        updates=command(repo,'diff','--name-only',base,remote,'--',model_directory).stdout.splitlines()
        if any(not x.startswith(prefix)for x in updates):raise ValueError('Remote model package changed since forecast; run again with the reviewed version')
        head=command(repo,'rev-parse','HEAD').stdout.strip()
        if remote!=head and remote!=publication_parent:
            rebased=command(repo,'rebase','--onto',remote,publication_parent,check=False)
            if rebased.returncode:
                command(repo,'rebase','--abort',check=False)
                raise RuntimeError('Generated forecast conflicts with remote changes; publication stopped without a force push')
            publication_parent=remote
        validate_publication(root)
        pushed=command(repo,'push','origin','HEAD:refs/heads/'+branch,check=False)
        if pushed.returncode==0:return {'complete':True,'commit':command(repo,'rev-parse','HEAD').stdout.strip(),'changed':True,'push_attempts':attempt}
        # Retry only non-fast-forward races. Protection/auth/network failures
        # remain visible immediately and never weaken branch protection.
        if not any(term in pushed.stderr.lower()for term in ['non-fast-forward','fetch first']):raise RuntimeError('Normal publication push rejected: '+pushed.stderr.strip())
    raise RuntimeError('Remote moved during all three normal push attempts; publication stopped')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',default='.');p.add_argument('--model-directory',required=True);p.add_argument('--branch',required=True);a=p.parse_args()
    print(json.dumps(commit_latest(a.repo,a.model_directory,a.branch)))
