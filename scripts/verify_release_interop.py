#!/usr/bin/env python3
'''Fail closed unless the release uses the tested 01.03.15 runtime inputs.'''
import hashlib
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    'assets/bin/Data/Managed/Metadata/global-metadata.dat': '80458f43b81c87a5baed157c1492292119b231dc521dd6aab578a8607cbe69c7',
    'lib/arm64-v8a/libil2cpp.so': '478deaad5e43477661988303a75b02f6fdca5239a63ba7a553194206e2cfb59d',
}

def verify(apk):
    with zipfile.ZipFile(apk) as archive:
        for name, expected in EXPECTED.items():
            with archive.open(name) as source:
                actual = hashlib.file_digest(source, 'sha256').hexdigest()
            if actual != expected:
                raise ValueError(f'{name}: {actual} does not match the tested interop inputs; regenerate and verify interop before publishing')
    static = ROOT / 'static/lemon/runtime/interop'
    files = list(static.iterdir())
    if len(files) != 121 or any(not p.is_file() or p.suffix != '.dll' or p.stat().st_size == 0 for p in files):
        raise ValueError('Expected 121 nonempty static interop DLLs')
    if (ROOT / 'static/libmain/libmain.so').stat().st_size != 1272968:
        raise ValueError('LemonLoader host is missing or has unexpected size')
    # The assembler prefers work/interop. Replace any unverified CI output.
    generated = ROOT / 'work/interop'
    if generated.exists():
        shutil.rmtree(generated)
    shutil.copytree(static, generated)
    print('Release interop inputs verified; using the tested 121 DLLs')

if __name__ == '__main__':
    try:
        verify(sys.argv[1])
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        sys.exit(f'Release blocked: {exc}')
