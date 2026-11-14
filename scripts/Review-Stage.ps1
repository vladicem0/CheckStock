[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Start', 'Review', 'Status')]
    [string]$Action,
    [Parameter(Mandatory = $true)]
    [string]$Stage,
    [string]$Repo = (Join-Path $PSScriptRoot '..'),
    [string]$StateDir,
    [string]$GitUrl = 'git@github-personal:vladicem0/CheckStock.git',
    [string]$SshHost = 'vpn',
    [string]$SshUser = 'dev',
    [string]$RemoteScript = '/home/dev/bin/review-stage.sh',
    [int]$TimeoutSeconds = 960
)

$ErrorActionPreference = 'Stop'
try {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $controllerPath = Join-Path $PSScriptRoot 'review/local_review.py'
    $controllerArguments = @(
        $controllerPath, $Action.ToLowerInvariant(),
        '--stage', $Stage,
        '--repo', $Repo,
        '--git-url', $GitUrl,
        '--ssh-host', $SshHost,
        '--ssh-user', $SshUser,
        '--remote-script', $RemoteScript,
        '--timeout', $TimeoutSeconds.ToString()
    )
    if ($StateDir) {
        $controllerArguments += @('--state-dir', $StateDir)
    }
    & $pythonCommand.Source @controllerArguments
    exit $LASTEXITCODE
}
catch {
    @{
        status = 'BLOCKED'
        stage = $Stage
        summary = "The review controller could not start: $($_.Exception.Message)"
    } | ConvertTo-Json -Compress
    exit 3
}
