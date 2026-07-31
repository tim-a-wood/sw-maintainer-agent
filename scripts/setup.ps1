# Maintain — install or update, one script for both.
# Run it from the repository checkout. Run it again to update.
#
# This file only finds Python and starts scripts/setup.py; every
# decision lives there, where the test suite can exercise it.

$ErrorActionPreference = "Stop"

$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Error ("Python is absent. Install Python 3.11 or later from " +
                 "python.org, with the py launcher. Then run this " +
                 "script again.")
}

& py -3 "$PSScriptRoot\setup.py"
exit $LASTEXITCODE
