$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Run-Python {
    & python @args
    if ($LASTEXITCODE -ne 0) { throw "Python command failed ($LASTEXITCODE)" }
}

Run-Python -m unittest discover -s tests -v
Run-Python build_price_ocr.py "$PSScriptRoot/build/helper_dist/PriceOCR.exe"
Run-Python -m PyInstaller --noconfirm --windowed --onedir --name V26Macro --icon "$PSScriptRoot/assets/app.ico" --distpath "$PSScriptRoot/dist" --workpath "$PSScriptRoot/build/main" --specpath "$PSScriptRoot/build" "$PSScriptRoot/app.py"
Run-Python -m PyInstaller --noconfirm --windowed --onefile --name UpdateHelper --distpath "$PSScriptRoot/build/helper_dist" --workpath "$PSScriptRoot/build/helper" --specpath "$PSScriptRoot/build" "$PSScriptRoot/updater.py"
Copy-Item -LiteralPath "$PSScriptRoot/build/helper_dist/UpdateHelper.exe" -Destination "$PSScriptRoot/dist/V26Macro/UpdateHelper.exe"
Copy-Item -LiteralPath "$PSScriptRoot/build/helper_dist/PriceOCR.exe" -Destination "$PSScriptRoot/dist/V26Macro/PriceOCR.exe"
New-Item -ItemType Directory -Force "$PSScriptRoot/dist/V26Macro/assets" | Out-Null
Copy-Item -LiteralPath "$PSScriptRoot/assets/catcher.png", "$PSScriptRoot/assets/app.ico", "$PSScriptRoot/assets/coach_advice.png", "$PSScriptRoot/assets/league_next_match.png", "$PSScriptRoot/assets/league_round_label.png", "$PSScriptRoot/assets/league_play_hud.png" -Destination "$PSScriptRoot/dist/V26Macro/assets"
Copy-Item -LiteralPath "$PSScriptRoot/README.md", "$PSScriptRoot/TEST_RESULTS.md" -Destination "$PSScriptRoot/dist/V26Macro"
$compatAssets="$PSScriptRoot/dist/V26Macro/_internal/compya_resources"
New-Item -ItemType Directory -Force $compatAssets | Out-Null
Copy-Item -LiteralPath "$PSScriptRoot/dist/V26Macro/PriceOCR.exe", "$PSScriptRoot/assets/coach_advice.png", "$PSScriptRoot/assets/league_next_match.png", "$PSScriptRoot/assets/league_round_label.png", "$PSScriptRoot/assets/league_play_hud.png" -Destination $compatAssets
$leagueSafetyAssets = @('ball_shop_description.png','ball_shop_icon.png','ball_result_icon.png','ball_result_disabled.png','ball_info_title.png','ball_info_icon.png','league_bonus_ready.png','league_bonus_title.png','league_bonus_close.png','league_bonus_close_v2.png','league_bonus_done.png','ball_balance_icon.png','coach_title_v2.png','coach_cancel_v2.png')
foreach ($assetName in $leagueSafetyAssets) {
    Copy-Item -LiteralPath "$PSScriptRoot/assets/$assetName" -Destination "$PSScriptRoot/dist/V26Macro/assets/$assetName"
    Copy-Item -LiteralPath "$PSScriptRoot/assets/$assetName" -Destination "$compatAssets/$assetName"
}
$sponsorAssets = @('sponsor_title.png','sponsor_settle.png','sponsor_reward_title.png','sponsor_reward_confirm.png','sponsor_home_tab.png','sponsor_home_play.png','sponsor_result_confirm.png')
foreach ($assetName in $sponsorAssets) {
    Copy-Item -LiteralPath "$PSScriptRoot/assets/$assetName" -Destination "$PSScriptRoot/dist/V26Macro/assets/$assetName"
    Copy-Item -LiteralPath "$PSScriptRoot/assets/$assetName" -Destination "$compatAssets/$assetName"
}
$testProcess = Start-Process -FilePath "$PSScriptRoot/dist/V26Macro/V26Macro.exe" -ArgumentList '--self-test' -WindowStyle Hidden -PassThru
if (-not $testProcess.WaitForExit(30000)) { throw 'Executable self-test timed out' }
$testProcess.Refresh()
if ($testProcess.ExitCode -ne 0) { throw 'Executable self-test failed' }
Run-Python package_release.py
