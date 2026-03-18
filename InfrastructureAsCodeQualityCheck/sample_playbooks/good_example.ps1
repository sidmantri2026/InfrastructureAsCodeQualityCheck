#Requires -Version 5.1
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Password = Get-Secret -Name 'AppDbPassword' -AsPlainText

function Get-UserData {
    <#
    .SYNOPSIS
        Retrieves user data from the remote server.
    .PARAMETER Server
        Target server hostname.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)]
        [string]$Server,
        [Parameter(Mandatory=$true)]
        [string]$Username
    )
    try {
        $params = @{
            Uri         = "https://$Server/api/users/$Username"
            Method      = 'Get'
            ErrorAction = 'Stop'
        }
        $result = Invoke-RestMethod @params
        return [PSCustomObject]@{ Username = $Username; Data = $result }
    }
    catch {
        Write-Error "Failed to get user data from $Server: $_"
        exit 1
    }
}
