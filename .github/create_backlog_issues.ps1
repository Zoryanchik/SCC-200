param(
    [string]$Repo = "Zoryanchik/SCC-200",
    [string]$DraftFile = ".github/ISSUE_DRAFTS.md"
)

$ErrorActionPreference = "Stop"

function Get-RepoToken {
    param([string]$RepoName)

    $request = "protocol=https`nhost=github.com`npath=$RepoName.git`n`n"
    $credentialText = $request | git credential fill
    $pairs = @{}

    foreach ($line in ($credentialText -split "`n")) {
        if ($line -match "^(.*?)=(.*)$") {
            $pairs[$matches[1]] = $matches[2].Trim()
        }
    }

    if (-not $pairs.ContainsKey("password") -or -not $pairs["password"]) {
        throw "No HTTPS credential was returned for $RepoName."
    }

    return $pairs["password"]
}

function Get-IssueDrafts {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        throw "Draft file not found: $Path"
    }

    $content = Get-Content $Path -Raw
    $pattern = '(?ms)^##\s+\d+\.\s+(?<title>.+?)\r?\nLabels:\s*(?<labels>.+?)\r?\n\r?\n(?<body>.*?)(?=^##\s+\d+\.|\z)'
    $matches = [regex]::Matches($content, $pattern)

    if ($matches.Count -eq 0) {
        throw "No issue drafts were parsed from $Path."
    }

    return @(
        foreach ($match in $matches) {
            [pscustomobject]@{
                Title = $match.Groups["title"].Value.Trim()
                Labels = $match.Groups["labels"].Value.Trim()
                Body = $match.Groups["body"].Value.Trim()
            }
        }
    )
}

$token = Get-RepoToken -RepoName $Repo
$headers = @{
    Authorization = "Bearer $token"
    Accept = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
}

$drafts = Get-IssueDrafts -Path $DraftFile
$existingIssues = Invoke-RestMethod -Headers $headers -Uri "https://api.github.com/repos/$Repo/issues?state=all&per_page=100"
$existingTitles = @{}

foreach ($issue in @($existingIssues)) {
    if ($null -eq $issue.pull_request) {
        $existingTitles[$issue.title] = $true
    }
}

$results = New-Object System.Collections.Generic.List[string]

foreach ($draft in $drafts) {
    if ($existingTitles.ContainsKey($draft.Title)) {
        $results.Add("SKIPPED | $($draft.Title)")
        continue
    }

    $payload = @{
        title = $draft.Title
        body = "Source: fix_plan.md`nLabels: $($draft.Labels)`n`n$($draft.Body)"
    } | ConvertTo-Json

    $created = Invoke-RestMethod -Method Post -Headers $headers -Uri "https://api.github.com/repos/$Repo/issues" -ContentType "application/json" -Body $payload
    $results.Add("CREATED | #$($created.number) | $($created.title) | $($created.html_url)")
    $existingTitles[$draft.Title] = $true
}

$results | Out-String -Width 240