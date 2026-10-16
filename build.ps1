$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Run-Python {
    & python @args
    if ($LASTEXITCODE -ne 0) { throw "Python command failed ($LASTEXITCODE)" }
}

Run-Python -m unittest discover -s tests -v
Run-Python -m PyInstaller --noconfirm --windowed --onedir --name V26Macro --icon "$PSScriptRoot/assets/app.ico" --distpath "$PSScriptRoot/dist" --workpath "$PSScriptRoot/build/main" --specpath "$PSScriptRoot/build" "$PSScriptRoot/app.py"
Run-Python -m PyInstaller --noconfirm --windowed --onefile --name UpdateHelper --distpath "$PSScriptRoot/build/helper_dist" --workpath "$PSScriptRoot/build/helper" --specpath "$PSScriptRoot/build" "$PSScriptRoot/updater.py"
Copy-Item -LiteralPath "$PSScriptRoot/build/helper_dist/UpdateHelper.exe" -Destination "$PSScriptRoot/dist/V26Macro/UpdateHelper.exe"
New-Item -ItemType Directory -Force "$PSScriptRoot/dist/V26Macro/assets" | Out-Null
Copy-Item -LiteralPath "$PSScriptRoot/assets/catcher.png", "$PSScriptRoot/assets/app.ico" -Destination "$PSScriptRoot/dist/V26Macro/assets"
Copy-Item -LiteralPath "$PSScriptRoot/README.md", "$PSScriptRoot/TEST_RESULTS.md" -Destination "$PSScriptRoot/dist/V26Macro"
$testProcess = Start-Process -FilePath "$PSScriptRoot/dist/V26Macro/V26Macro.exe" -ArgumentList '--self-test' -WindowStyle Hidden -PassThru
if (-not $testProcess.WaitForExit(30000)) { throw 'Executable self-test timed out' }
$testProcess.Refresh()
if ($testProcess.ExitCode -ne 0) { throw 'Executable self-test failed' }
Run-Python package_release.py
