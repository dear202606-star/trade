"""Build UI over the last deployed financial snapshot, without recalculation."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request
import urllib.error

import site_build

BASE = Path(__file__).resolve().parent
REPO = 'dear202606-star/trade'
SITE = 'https://dear202606-star.github.io/trade/'


def gh(*args):
    return subprocess.run(['gh',*args],check=True,capture_output=True,text=True).stdout


def data_hashes(target):
    return {str(p.relative_to(target)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (target/'data').rglob('*') if p.is_file()}


def run(archive=None):
    with tempfile.TemporaryDirectory() as directory:
        run_id = None
        if archive is None:
            artifacts = json.loads(gh('api','repos/'+REPO+'/actions/artifacts?name=github-pages&per_page=30'))['artifacts']
            for item in artifacts:
                if item['expired'] or item['workflow_run'].get('head_branch') != 'main':
                    continue
                candidate = item['workflow_run']['id']
                run_info = json.loads(gh('api','repos/'+REPO+'/actions/runs/'+str(candidate)))
                if run_info['conclusion'] == 'success':
                    run_id = candidate
                    break
            if run_id is None:
                raise RuntimeError('No successful retained Pages snapshot; do not regenerate financial history')
            gh('run','download',str(run_id),'--repo',REPO,'--name','github-pages','--dir',directory)
            archive = Path(directory)/'artifact.tar'
        target = BASE/'_site_release'
        target.mkdir(exist_ok=True)
        with tarfile.open(archive) as package:
            members = package.getmembers()
            if sum(m.size for m in members)>100*1024**2:
                raise RuntimeError('Pages snapshot exceeds size bound')
            for member in members:
                path = (target/member.name).resolve()
                if not path.is_relative_to(target.resolve()) or not (member.isfile() or member.isdir()):
                    raise RuntimeError('Unsafe Pages archive entry')
            package.extractall(target,members=[m for m in members if not m.name.endswith('.md')])
        # Confirm that the critical portfolio records match what visitors see.
        for name in ('equity','signal'):
            for variant in ('','b/'):
                relative = 'data/'+variant+name+'.json'
                try:
                    with urllib.request.urlopen(SITE+relative+'?ui_baseline=1',timeout=20) as response:
                        deployed = json.load(response)
                except urllib.error.HTTPError as exc:
                    # 2026-11-06 起静态数据不再发布到 Pages，改由在线库承载；
                    # 线上已移除时跳过逐字节比对，仍保留本地快照作为迁移源。
                    if exc.code == 404:
                        continue
                    raise
                local = json.loads((target/relative).read_text())
                if local != deployed:
                    raise RuntimeError('Pages baseline differs from current deployed data: '+relative)
        before = data_hashes(target)
        if not before:
            raise RuntimeError('No financial snapshot to preserve')
        original_docs = site_build.DOCS
        try:
            site_build.DOCS = str(target)
            site_build.copy_site()
            site_build.bust_cache()
        finally:
            site_build.DOCS = original_docs
        if before != data_hashes(target):
            raise RuntimeError('UI build modified financial records')
        print(json.dumps({'baseline_run_id':run_id,'financial_files_preserved':len(before),
                          'financial_sha256':before,'strategy_recalculation':False}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path)
    run(parser.parse_args().archive)
