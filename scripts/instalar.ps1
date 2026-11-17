$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
try {
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uvCommand) {
        $candidate = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
        if (Test-Path -LiteralPath $candidate) { $uvCommand = Get-Item -LiteralPath $candidate }
    }
    if ($uvCommand) {
        $uvExecutable = if ($uvCommand.Source) { $uvCommand.Source } else { $uvCommand.FullName }
        & $uvExecutable venv --python 3.12 .venv
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno con uv.' }
        & $uvExecutable pip install --python '.venv\Scripts\python.exe' -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias.' }
    } else {
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        $python = Get-Command python -ErrorAction SilentlyContinue
        if ($launcher) {
            & $launcher.Source -3.12 -m venv .venv
        } elseif ($python) {
            & $python.Source -c 'import sys; assert (3,12) <= sys.version_info[:2] < (3,14), "Usa Python 3.12 o 3.13"'
            if ($LASTEXITCODE -ne 0) { throw 'Instala Python 3.12 desde python.org.' }
            & $python.Source -m venv .venv
        } else { throw 'Instala Python 3.12 desde https://www.python.org/downloads/ y vuelve a ejecutar INSTALAR.cmd.' }
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno. Instala Python 3.12.' }
        & '.\.venv\Scripts\python.exe' -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo actualizar pip.' }
        & '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias.' }
    }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
