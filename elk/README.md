# Lokale ELK Stack voor OSINT-analyse

Deze ELK Stack verwerkt AI-verrijkte OSINT-resultaten en maakt analyse mogelijk in Kibana.

Elasticsearch slaat de resultaten op.

Logstash leest automatisch het nieuwste JSONL-bestand en importeert de records naar Elasticsearch.

Kibana laat je de gegevens analyseren op tijd, locatie, AI-label, broncategorie en platform.

## Mappenstructuur

```text
OSINT-ELKStack/
|-- docker-compose.yml
|-- README.md
|-- data/
|   `-- latest_osint_ai.jsonl
|-- scripts/
|   `-- update-latest-ai.ps1
`-- logstash/
    |-- pipeline/
    |   `-- osint-ai.conf
    `-- templates/
        `-- osint-results-template.json
```

## Bestanden

| Bestand                                        | Doel                                                               |
| ---------------------------------------------- | ------------------------------------------------------------------ |
| docker-compose.yml                             | Start Elasticsearch, Logstash en Kibana                            |
| logstash/pipeline/osint-ai.conf                | Leest latest_osint_ai.jsonl en schrijft records naar Elasticsearch |
| logstash/templates/osint-results-template.json | Definieert de veldtypes                                            |
| scripts/update-latest-ai.ps1                   | Selecteert automatisch de nieuwste AI-output                       |
| data/                                          | Bevat AI-outputbestanden                                           |

## Installatie

Open PowerShell:

```powershell
cd "K:\HSLeiden Onderzoeksomgevingen\OSINT\OSINT-ELKStack"
```

Maak of update eerst het latest-bestand:

```powershell
.\scripts\update-latest-ai.ps1
```

Start daarna de ELK Stack:

```powershell
docker compose up -d
```

## Toegang

Kibana:

```text
http://localhost:5601
```

Elasticsearch:

```text
http://localhost:9200
```

## Nieuw AI-bestand verwerken

Wanneer een nieuw bestand beschikbaar is, bijvoorbeeld:

```text
output/osint_results_ai_v5.jsonl
```

voer je uit:

```powershell
.\scripts\update-latest-ai.ps1 -RestartLogstash
```

Het script:

1. Zoekt automatisch de hoogste versie.
2. Maakt latest_osint_ai.jsonl opnieuw aan.
3. Herstart Logstash.
4. Importeert de nieuwe records.

Je hoeft geen configuratiebestanden aan te passen.

## Docker Commands

Starten:

```powershell
docker compose up -d
```

Stoppen:

```powershell
docker compose down
```

Volumes verwijderen:

```powershell
docker compose down -v
```

Logs bekijken:

```powershell
docker compose logs -f
docker compose logs -f logstash
docker compose logs -f elasticsearch
docker compose logs -f kibana
```

## Elasticsearch Controle

Aantal records:

```powershell
curl http://localhost:9200/osint-results/_count
```

Documenten bekijken:

```powershell
curl http://localhost:9200/osint-results/_search?pretty
```

Alle indices:

```powershell
curl http://localhost:9200/_cat/indices?v
```

## Belangrijkste Velden

De pipeline bewaart onder andere:

* platform
* title
* text
* published_at
* source_category
* content_type
* hashtags
* ai_label
* ai_score
* ai_model
* ai_detected_locations
* ai_location_count
* scrape_method
* scrape_version
* ai_output_version

Voor tijdlijnanalyse gebruikt Kibana het veld @timestamp. Logstash vult dit veld automatisch vanuit published_at.
