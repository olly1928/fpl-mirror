"""Verify and safely unpack a committed historical raw-data collection."""
import argparse
import hashlib
import json
import pathlib
import re
import tarfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def unpack(index_path, output):
    index = json.loads(index_path.read_text())
    folder = ROOT/index['directory']
    if not folder.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('Collection path escapes repository')
    members = 0
    for record in index['summary']['packages']:
        path = folder/record['file']; raw = path.read_bytes()
        if len(raw) != record['bytes'] or hashlib.sha256(raw).hexdigest() != record['sha256']:
            raise ValueError('Raw package checksum mismatch: '+str(path))
        match = re.fullmatch(r'archive-(\d{4}-\d{2})\.tar\.gz', record['file'])
        destination = output/'archive'/match.group(1) if match else output/'official'
        if not match and not re.fullmatch(r'official-fpl-\d{4}-\d{2}\.tar\.gz', record['file']):
            raise ValueError('Unexpected package name')
        with tarfile.open(path, 'r:gz') as archive:
            items = archive.getmembers()
            if len(items) != record['members']:
                raise ValueError('Package member count mismatch')
            for member in items:
                relative = pathlib.Path(member.name)
                if not member.isfile() or relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('Unsafe archive member: '+member.name)
                target = destination/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read()); members += 1
        for meta in destination.rglob('*.meta.json'):
            metadata = json.loads(meta.read_text())
            source = meta.with_name(meta.name[:-len('.meta.json')])
            if hashlib.sha256(source.read_bytes()).hexdigest() != metadata['sha256']:
                raise ValueError('Source bytes differ from capture manifest: '+str(source))
    return {'packages':len(index['summary']['packages']), 'members':members,
            'source_run':index['run_id'], 'output':str(output)}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--index', type=pathlib.Path, default=ROOT/'history/collections/latest.json')
    p.add_argument('--output', type=pathlib.Path, required=True)
    a = p.parse_args(); print(json.dumps(unpack(a.index, a.output)))
