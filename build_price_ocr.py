"""Compile the local Windows OCR helper with the installed .NET Framework compiler."""
import os
from pathlib import Path
import subprocess
import sys


def build(output):
    windows=Path(os.environ.get('SystemRoot',r'C:\Windows'))
    framework=windows/'Microsoft.NET'/'Framework64'/'v4.0.30319'
    if not (framework/'csc.exe').exists():framework=windows/'Microsoft.NET'/'Framework'/'v4.0.30319'
    metadata=windows/'System32'/'WinMetadata'
    output=Path(output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    refs=[framework/'System.Runtime.dll']+[metadata/(name+'.winmd') for name in
          ('Windows.Foundation','Windows.Storage','Windows.Graphics','Windows.Media','Windows.Globalization')]
    command=[str(framework/'csc.exe'),'/nologo','/target:exe','/out:'+str(output)]
    command+=['/r:'+str(path) for path in refs]
    command.append(str(Path(__file__).resolve().with_name('PriceOCR.cs')))
    subprocess.run(command,check=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


if __name__=='__main__':build(sys.argv[1] if len(sys.argv)>1 else 'build/helper_dist/PriceOCR.exe')
