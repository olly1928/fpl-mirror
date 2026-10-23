"""Fill the same verified object cache from a pinned GitHub ZIP in one request.

This avoids thousands of small requests. Only inventoried files are read from
the ZIP; every extracted body must match the independently listed Git blob SHA.
"""
import argparse
import datetime
import hashlib
import json
import pathlib
import urllib.request
import zipfile


def fill(inventory, output, repository):
    rows=[r for r in json.loads(inventory.read_text())['files'] if r['repository']==repository]
    revisions={r['commit'] for r in rows}
    if len(revisions)!=1:raise ValueError('One pinned revision required')
    revision=next(iter(revisions));url='https://codeload.github.com/'+repository+'/zip/'+revision
    zpath=output.parent/'zip-cache'/(repository.replace('/','-')+'-'+revision+'.zip')
    zpath.parent.mkdir(parents=True,exist_ok=True)
    if not zpath.exists():
        part=zpath.with_suffix('.partial');total=0
        with urllib.request.urlopen(url,timeout=120) as response,part.open('wb') as stream:
            while True:
                chunk=response.read(8*1024*1024)
                if not chunk:break
                stream.write(chunk);total+=len(chunk)
                if total%(64*1024*1024)==0:print(json.dumps({'repository':repository,'downloaded_bytes':total}),flush=True)
        part.replace(zpath)
    (output/'objects').mkdir(parents=True,exist_ok=True);(output/'metadata').mkdir(parents=True,exist_ok=True)
    now=datetime.datetime.now(datetime.timezone.utc).isoformat()
    with zipfile.ZipFile(zpath) as source:
        prefix=source.namelist()[0].split('/')[0]+'/'
        for row in rows:
            body=source.read(prefix+row['path'])
            actual=hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()
            if actual!=row['git_blob_sha'] or len(body)!=row['bytes']:raise ValueError('ZIP source differs from inventory')
            (output/'objects'/actual).write_bytes(body)
            meta={'url':url,'status':200,'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest(),
                  'captured_at':now,'transport':'pinned-github-zip','archive_member':prefix+row['path']}
            (output/'metadata'/(actual+'.json')).write_text(json.dumps(meta,sort_keys=True)+'\n')
    print(json.dumps({'repository':repository,'verified_paths':len(rows),'zip_bytes':zpath.stat().st_size}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inventory',type=pathlib.Path,required=True)
    p.add_argument('--output',type=pathlib.Path,required=True);p.add_argument('--repository',required=True)
    a=p.parse_args();fill(a.inventory,a.output,a.repository)
