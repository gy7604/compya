"""Release only a fixed runtime allowlist; user data is never packaged."""
from pathlib import Path
import hashlib
import zipfile


def build_archive(root, output):
    root, output = Path(root), Path(output)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(root.rglob('*')):
            rel = path.relative_to(root)
            permitted = (len(rel.parts) == 1 and rel.name in (
                'V26Macro.exe', 'UpdateHelper.exe', 'README.md', 'TEST_RESULTS.md')) or (
                rel.parts[0] == '_internal') or (
                len(rel.parts) == 2 and rel.parts[0] == 'assets' and rel.name in ('catcher.png', 'app.ico'))
            if path.is_file() and permitted:
                z.write(path, Path('V26Macro')/rel)
    with zipfile.ZipFile(output) as z:
        if z.testzip() is not None:
            raise ValueError('Release ZIP validation failed')
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.sha256').write_text(f'{checksum}  {output.name}\n', encoding='ascii')
    return checksum


if __name__ == '__main__':
    here = Path(__file__).resolve().parent
    out = here/'releases'
    out.mkdir(exist_ok=True)
    print(build_archive(here/'dist'/'V26Macro', out/'V26Macro_Windows.zip'))
