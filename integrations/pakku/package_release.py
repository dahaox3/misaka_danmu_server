"""Package built extensions and corresponding source; never include run data."""
import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def tracked(directory):
    output = subprocess.check_output(['git', '-C', str(directory), 'ls-files', '-z'])
    return [directory / name.decode() for name in output.split(b'\0') if name]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=HERE / 'pakku-release.tar.gz')
    args = parser.parse_args()
    source = HERE / 'pakku-integration-source.tar.gz'
    vendor = HERE / 'vendor/pakku'
    with tarfile.open(source, 'w:gz') as archive:
        for path in tracked(ROOT) + tracked(vendor):
            if path.is_file() and not path.is_symlink():
                archive.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
    (ROOT / 'web/dist/pakku-integration-source.tar.gz').write_bytes(source.read_bytes())
    files = ('src/core/pakku_recent.py', 'src/core/pakku_settings.py',
             'src/api/ui/pakku_config.py', 'src/api/dandan/pakku_merge.py', 'src/api/dandan/pakku_output.py')
    with tarfile.open(args.output, 'w:gz') as archive:
        for name in files:
            archive.add(ROOT / name, arcname='overrides/' + name)
        archive.add(ROOT / 'web/dist', arcname='overrides/web-dist')
        for name in ('pakku_gateway.py', 'pakku_entrypoint.py', 'run-gateway.sh'):
            archive.add(HERE / name, arcname='gateway/' + name)
        for name in ('engine.mjs', 'server.mjs', 'similarity-gen.wasm', 'LICENSE.pakku.txt', 'README.md', 'provenance.json'):
            archive.add(HERE / name, arcname='pakku/' + name)
    print(json.dumps({'file': str(args.output), 'bytes': args.output.stat().st_size,
                      'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
