"""Build UI over the last deployed financial snapshot, without recalculation."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import urllib.error

import site_build

BASE = Path(__file__).resolve().parent
REPO = 'dear202606-star/trade'
SITE = 'https://dear202606-star.github.io/trade/'
RETRYABLE_HTTP = (429, 500, 502, 503, 504)


def gh(*args, attempts=3):
    """GitHub API / artifact 下载偶发 5xx 时退避重试，避免整条发布链被抖动打断。"""
    last = ''
    for attempt in range(attempts):
        done = subprocess.run(['gh',*args],capture_output=True,text=True)
        if done.returncode == 0:
            return done.stdout
        last = (done.stderr or done.stdout).strip()[-300:]
        if attempt < attempts - 1:
            time.sleep(2 ** attempt)
    raise RuntimeError('gh %s 失败（%d 次）：%s' % (' '.join(args[:2]), attempts, last))


def fetch_json(url, timeout=20, attempts=4):
    """读取 Pages 基线；404 或重试耗尽返回 None，由调用方降级为「跳过比对」。

    2026-11-10：实测 Pages 偶发 503 会让 build 直接抛 HTTPError，deploy 沿
    needs 链被跳过，线上界面因此停在旧版本。静态金融数据早已迁到在线库，
    Pages 上的旧 JSON 只是迁移源；基线短暂不可读不应阻断发布。真正读到
    且内容不一致时仍然 fail-closed（见下方比对）。
    """
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url,timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code not in RETRYABLE_HTTP:
                raise
        except (urllib.error.URLError, TimeoutError, ValueError):
            pass
        if attempt < attempts - 1:
            time.sleep(2 ** attempt)
    return None


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
                deployed = fetch_json(SITE+relative+'?ui_baseline=1')
                if deployed is None:
                    # 静态数据已迁出 Pages（404）或 Pages 短暂 5xx：两者都
                    # 不构成「基线不一致」，跳过逐字节比对，保留本地快照。
                    print('::warning::Pages 基线不可读，跳过比对：'+relative)
                    continue
                local = json.loads((target/relative).read_text())
                if local != deployed:
                    raise RuntimeError('Pages baseline differs from current deployed data: '+relative)
        # 2026-11-08：引导配置必须来自 site_src/cloud.json 单一来源，而不能
        # 沿用上一份 Pages artifact —— 否则新增的 Turso 备库端点永远不会
        # 发布到线上。放在 before 之前，使 data_hashes 前后比对包含同一份配置。
        site_build.write_cloud_config(str(target / "data"))
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
