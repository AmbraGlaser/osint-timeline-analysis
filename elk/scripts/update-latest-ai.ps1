<# 
  Bepaalt automatisch het nieuwste AI-outputbestand.

  Zoekt in de outputmap van het scraper-project naar:
    osint_results_ai_v*.jsonl

  Selecteert de hoogste versie, bijvoorbeeld:
    osint_results_ai_v5.jsonl

  Kopieert die naar de lokale ELK Stack outputmap:
    OSINT-ELKStack/output/latest_osint_ai.jsonl

  Logstash hoeft daardoor nooit een hardcoded versienummer te kennen.
#>

[CmdletBinding()]
param(
    # Bronmap waarin de scraper en AI-classifier hun JSONL-bestanden plaatsen.
    [string]$SourceDirectory = "K:\HSLeiden Onderzoeksomgevingen\OSINT\OSINT_scraping-script\osint_scraper\output",

    # Doelmap waar Logstash het vaste latest-bestand leest.
    [string]$DestinationDirectory = (Join-Path $PSScriptRoot "..\data"),

    # Naam van het vaste bestand dat Logstash leest.
    [string]$LatestFileName = "latest_osint_ai.jsonl",

    # Herstart optioneel Logstash na het bijwerken, zodat het nieuwste bestand direct opnieuw wordt ingelezen.
    [switch]$RestartLogstash
)

$ErrorActionPreference = "Stop"

$resolvedSourceDirectory = Resolve-Path -LiteralPath $SourceDirectory
$resolvedDestinationDirectory = Resolve-Path -LiteralPath $DestinationDirectory
$latestPath = Join-Path $resolvedDestinationDirectory $LatestFileName

# Zoek alleen AI-outputbestanden met een versienummer in de bestandsnaam.
$candidates = Get-ChildItem -LiteralPath $resolvedSourceDirectory -File -Filter "osint_results_ai_v*.jsonl" |
    ForEach-Object {
        if ($_.Name -match '^osint_results_ai_v(?<version>\d+)\.jsonl$') {
            [PSCustomObject]@{
                File = $_
                Version = [int]$Matches.version
            }
        }
    }

if (-not $candidates) {
    throw "Geen AI-outputbestanden gevonden in '$resolvedSourceDirectory' met patroon osint_results_ai_v*.jsonl."
}

$newest = $candidates | Sort-Object Version -Descending | Select-Object -First 1

# Kopie is op Windows betrouwbaarder dan symlink, omdat symlinks vaak extra rechten vereisen.
Copy-Item -LiteralPath $newest.File.FullName -Destination $latestPath -Force

Write-Host "Nieuwste AI-outputbestand: $($newest.File.Name)"
Write-Host "Versie: $($newest.Version)"
Write-Host "Bronmap: $resolvedSourceDirectory"
Write-Host "Logstash-bestand bijgewerkt: $latestPath"

if ($RestartLogstash) {
    # Herstart alleen Logstash; Elasticsearch en Kibana blijven draaien.
    docker compose restart logstash
}
