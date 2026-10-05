# OSINT Timeline Analysis

Dit project is ontwikkeld tijdens de Minor Digital Forensics aan Hogeschool Leiden.

Het onderzoek richt zich op de vraag hoe openbaar beschikbare online informatie kan worden verzameld, gestructureerd en geanalyseerd om tijdlijnen en mogelijke locatiepatronen te reconstrueren.

## Projectworkflow

1. Openbare gegevens verzamelen uit verschillende online bronnen.
2. De verzamelde data normaliseren naar JSONL.
3. Records verrijken met rule-based en zero-shot classificatie.
4. De verrijkte dataset via Logstash importeren in Elasticsearch.
5. Tijdlijnen, broncategorieën en mogelijke locaties analyseren in Kibana.

## Databronnen

Het script gebruikt:

* YouTube RSS-feeds
* Openbare YouTube-transcripties
* Instagram-profielen
* X-profielen
* TikTok-profielen
* Google News RSS-feeds

Het script werkt zonder:

* login
* accountcookies
* API-keys
* technieken om beveiligingen te omzeilen

## Projectstructuur

```text
osint_scraper/
|-- config.example.py
|-- ai_classifier.py
|-- main.py
|-- urls.txt
|-- requirements.txt
|-- README.md
`-- output/
    |-- osint_results_v1.jsonl
    |-- osint_results_v2.jsonl
    |-- osint_results_v3.jsonl
    `-- osint_results_v4.jsonl
```

## Installatie

Installeer de vereiste libraries:

```bash
py -m pip install -r requirements.txt
```

Installeer daarna de Playwright browsers:

```bash
py -m playwright install
```

## Gebruikte libraries

* requests
* beautifulsoup4
* playwright
* feedparser
* youtube-transcript-api
* transformers
* torch
* python-dateutil

## Configuratie


Kopieer `config.example.py` naar `config.py`:

```bash
cp scraper/config.example.py scraper/config.py
```

```python
TARGET = {
    "name": "Example Name",
    "youtube": "ExampleChannel",
    "youtube_channel_id": "CHANNEL_ID",
    "instagram": "example",
    "x": "example",
    "tiktok": "example",
    "news_queries": [
        "https://news.google.com/rss/search?q=%22Example%20Name%22&hl=nl&gl=NL&ceid=NL:nl"
    ],
}
```

Voor een nieuw onderzoeksobject hoef je alleen deze waarden aan te passen.

### Extra URL's

Optioneel kunnen aanvullende openbare URL's worden toegevoegd aan `urls.txt`, één URL per regel.

Lege regels en regels die beginnen met `#` worden genegeerd.

Voorbeeld:

```text
https://example.com/public-page
https://www.instagram.com/example/
```

## Nieuwsartikelen

Het script verzamelt ook nieuwsartikelen via Google News RSS-feeds.

Per artikel wordt opgeslagen:

* titel
* publicatiedatum
* samenvatting
* URL

Duplicaten worden automatisch verwijderd.

## YouTube

YouTube-data wordt opgehaald via:

```text
https://www.youtube.com/feeds/videos.xml?channel_id=<CHANNEL_ID>
```

Per video maakt het script een afzonderlijk record.

Wanneer een openbaar transcript beschikbaar is, wordt dit automatisch toegevoegd.

## Instagram, X en TikTok

Playwright opent openbare profielpagina's.

Het script gebruikt:

* maximaal 3 scrollacties
* geen login
* geen cookies
* geen API-key

Per profiel wordt profielinformatie opgeslagen.

Wanneer berichten zichtbaar zijn, worden deze als afzonderlijke records toegevoegd.

### X als onderzoeksbevinding

X levert zonder login of API slechts beperkte resultaten op.

Wanneer geen berichten beschikbaar zijn, slaat het script alsnog profielinformatie op. Deze beperking vormt een onderzoeksbevinding.

## Uitvoering

Start de scraper:

```bash
py main.py
```

## AI-classificatie

Na het verzamelen van gegevens verrijkt ai_classifier.py de dataset automatisch.

Het script:

1. Zoekt de nieuwste scraper-output.
2. Combineert title, text en transcript.
3. Voert AI-classificatie uit.
4. Slaat een nieuwe AI-outputversie op.

Het model:

```text
facebook/bart-large-mnli
```

gebruikt de labels:

* locatie
* evenement
* persoon
* merk/bedrijf
* irrelevant

Toegevoegde velden:

* ai_label
* ai_score
* ai_processed_at
* ai_model
* ai_detected_locations
* ai_location_count
* source_category
* classification_pipeline
* source_dataset_version
* ai_output_version

## Automatisch versiebeheer

De scraper maakt altijd een nieuw bestand aan.

Bestanden worden nooit overschreven.

Voorbeeld:

```text
osint_results_v1.jsonl
osint_results_v2.jsonl
osint_results_v3.jsonl
```

Volgende run:

```text
osint_results_v4.jsonl
```

De AI-classifier werkt op dezelfde manier:

```text
osint_results_ai_v1.jsonl
osint_results_ai_v2.jsonl
osint_results_ai_v3.jsonl
```

De classifier kiest automatisch de nieuwste scraper-output.

## Outputformaat

De output gebruikt JSONL.

Elk record bevat onder andere:

* platform
* source_url
* scraped_at
* published_at
* title
* text
* hashtags
* possible_locations
* media_url
* content_type
* transcript
* scrape_method
* scrape_version

## Locatiedetectie

Het script herkent Nederlandse plaatsnamen in:

* titels
* beschrijvingen
* nieuwsartikelen
* transcripties

Gevonden plaatsnamen worden opgeslagen in:

```text
ai_detected_locations
```

Een gevonden plaatsnaam betekent niet automatisch dat de persoon zich daadwerkelijk op die locatie bevond.

## Beperkingen

Dit project is ontwikkeld als onderzoeksprototype en kent een aantal technische en methodologische beperkingen:

- De hoeveelheid en structuur van beschikbare data verschilt per platform, waardoor niet iedere bron op dezelfde manier kan worden verwerkt.
- Wijzigingen aan websites of HTML-structuren kunnen ervoor zorgen dat onderdelen van de scraper aangepast moeten worden.
- De kwaliteit van automatische classificatie is afhankelijk van de beschikbare tekst en het gebruikte classificatiemodel.
- Niet ieder record bevat voldoende context voor een betrouwbare inhoudelijke classificatie.
- De huidige locatiedetectie werkt op basis van een vooraf gedefinieerde lijst met Nederlandse plaatsnamen en herkent daardoor niet iedere mogelijke locatie.
- Het prototype is ontwikkeld voor kleinschalig onderzoek en is niet getest als productieomgeving of voor grootschalige dataverwerking.

## Ethische afbakening

Dit project is bedoeld voor onderwijs en praktijkonderzoek binnen OSINT.

Het project:

- gebruikt alleen openbaar beschikbare data
- gebruikt geen login
- gebruikt geen accountcookies
- gebruikt geen API-keys
- is niet gericht op afgeschermde privégegevens
- omzeilt geen beveiligingen
- respecteert platformbeperkingen
- behandelt resultaten als aanwijzingen en niet als bewezen feiten