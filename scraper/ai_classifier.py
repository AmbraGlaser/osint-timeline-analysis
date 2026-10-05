"""
AI-classificatie voor OSINT JSONL-output.

Dit script leest automatisch de nieuwste scraper-output, classificeert elk
record met een hybride aanpak en schrijft een verrijkte JSONL weg.
Regels bepalen de broncategorie; zero-shot AI bepaalt de inhoudelijke categorie.
"""

import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from transformers import pipeline


# Projectpaden.
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"

# Bestandspatronen voor versiebeheer.
SCRAPER_OUTPUT_PATTERN = re.compile(r"^osint_results_v(\d+)\.jsonl$")
AI_OUTPUT_PATTERN = re.compile(r"^osint_results_ai_v(\d+)\.jsonl$")
MAX_RECORDS = None

# Zero-shot model en inhoudelijke labels.
AI_MODEL = "facebook/bart-large-mnli"
CANDIDATE_LABELS = [
    "evenement",
    "locatie",
    "persoon",
    "merk/bedrijf",
    "irrelevant",
]

# Plaatsnamen voor AI-locatievelden.
DUTCH_PLACE_NAMES = [
    "Amsterdam",
    "Rotterdam",
    "Den Haag",
    "Utrecht",
    "Eindhoven",
    "Groningen",
    "Tilburg",
    "Breda",
    "Nijmegen",
    "Assen",
    "Zwolle",
    "Leiden",
    "Maastricht",
    "Haarlem",
    "Arnhem",
    "Almere",
    "Apeldoorn",
    "Den Bosch",
    "Leeuwarden",
    "Alkmaar",
    "Delft",
    "Hilversum",
    "Volendam",
    "Scheveningen",
    "Urk",
]


def setup_logging() -> None:
    """Stel logging in zodat de verwerking controleerbaar is."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def now_utc_iso() -> str:
    """Geef het huidige verwerkingstijdstip terug in UTC ISO-formaat."""
    return datetime.now(timezone.utc).isoformat()


def get_latest_scraper_output() -> Tuple[Path, str]:
    """Kies de nieuwste scraper-output op basis van versienummer."""
    highest_version = 0
    latest_file: Optional[Path] = None

    for file_path in OUTPUT_DIR.glob("osint_results_v*.jsonl"):
        match = SCRAPER_OUTPUT_PATTERN.match(file_path.name)
        if not match:
            continue

        version_number = int(match.group(1))
        if version_number > highest_version:
            highest_version = version_number
            latest_file = file_path

    if latest_file is None:
        raise FileNotFoundError("Geen scraper-outputbestand gevonden in output/.")

    source_version = f"v{highest_version}"
    logging.info("Inputbestand gevonden: %s", latest_file.name)
    logging.info("Gevonden inputversie: %s", source_version)
    return latest_file, source_version


def get_next_ai_output_file() -> Tuple[Path, str]:
    """Kies de volgende AI-outputversie zonder bestanden te overschrijven."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    highest_version = 0

    for file_path in OUTPUT_DIR.glob("osint_results_ai_v*.jsonl"):
        match = AI_OUTPUT_PATTERN.match(file_path.name)
        if match:
            highest_version = max(highest_version, int(match.group(1)))

    next_version = highest_version + 1
    ai_output_version = f"v{next_version}"
    output_file = OUTPUT_DIR / f"osint_results_ai_{ai_output_version}.jsonl"

    logging.info("AI-outputbestand: %s", output_file.name)
    logging.info("Nieuwe AI-outputversie: %s", ai_output_version)
    return output_file, ai_output_version


def combine_ai_input(record: Dict[str, object]) -> str:
    """Combineer title, text en transcript tot een enkele classifiertekst."""
    text_parts = []

    for field_name in ["title", "text", "transcript"]:
        value = record.get(field_name)
        if isinstance(value, str) and value.strip():
            text_parts.append(value.strip())

    combined_text = "\n\n".join(text_parts)
    return re.sub(r"\s+", " ", combined_text).strip()


def detect_locations_in_text(text: str) -> list:
    """
    Herken plaatsnamen met woordgrenzen.
    """
    found_locations = []

    for place_name in DUTCH_PLACE_NAMES:
        pattern = rf"\b{re.escape(place_name)}\b"
        if re.search(pattern, text or "", flags=re.IGNORECASE):
            found_locations.append(place_name)

    return sorted(set(found_locations), key=str.lower)


def add_location_fields(record: Dict[str, object], ai_input: str) -> Dict[str, object]:
    """Voeg AI-velden toe voor mogelijke locaties zonder possible_locations te wijzigen."""
    locations = detect_locations_in_text(ai_input)
    record["ai_detected_locations"] = locations
    record["ai_location_count"] = len(locations)
    return record


def get_source_category(record: Dict[str, object]) -> Optional[str]:
    """
    Bepaal de broncategorie op basis van platform en content_type.
    """
    platform = record.get("platform")
    content_type = record.get("content_type")

    if platform == "News":
        return "nieuws"

    if platform == "YouTube" and content_type in ["video", "short"]:
        return "video"

    if content_type == "profile":
        return "profielinformatie"

    if platform in ["Instagram", "X", "TikTok"] and content_type == "post":
        return "social_media_post"

    return None


def add_empty_ai_fields(
    record: Dict[str, object],
    source_dataset_version: str,
    ai_output_version: str,
) -> Dict[str, object]:
    """Vul AI-velden in wanneer er geen bruikbare tekst is."""
    record = add_location_fields(record, "")
    record["ai_label"] = "onbekend"
    record["ai_score"] = 0
    record["ai_processed_at"] = now_utc_iso()
    record["ai_model"] = AI_MODEL
    record["ai_input_length"] = 0
    record["classification_pipeline"] = "no_text"
    record["source_category"] = get_source_category(record)
    record["source_dataset_version"] = source_dataset_version
    record["ai_output_version"] = ai_output_version
    return record


def classify_record(
    record: Dict[str, object],
    classifier,
    source_dataset_version: str,
    ai_output_version: str,
) -> Tuple[Dict[str, object], str]:
    """
    Classificeer een enkel record en behoud alle bestaande velden.

    De status is: classified, no_text of error.
    """
    ai_input = combine_ai_input(record)
    input_length = len(ai_input)
    source_category = get_source_category(record)
    record = add_location_fields(record, ai_input)

    if not ai_input:
        return add_empty_ai_fields(record, source_dataset_version, ai_output_version), "no_text"

    try:
        # AI bepaalt de inhoudelijke categorie wanneer er tekst is.
        result = classifier(ai_input, CANDIDATE_LABELS, multi_label=False)
        record["ai_label"] = result["labels"][0]
        record["ai_score"] = float(result["scores"][0])
        record["ai_processed_at"] = now_utc_iso()
        record["ai_model"] = AI_MODEL
        record["ai_input_length"] = input_length
        record["source_category"] = source_category
        record["classification_pipeline"] = "rule_based_source_plus_zero_shot_content"
        record["source_dataset_version"] = source_dataset_version
        record["ai_output_version"] = ai_output_version
        return record, "zero_shot"
    except Exception as error:
        # Een fout record mag de verwerking niet stoppen.
        logging.warning("Classificatie mislukt voor record: %s", error)
        return add_empty_ai_fields(record, source_dataset_version, ai_output_version), "error"


def process_jsonl_file(
    input_file: Path,
    output_file: Path,
    source_dataset_version: str,
    ai_output_version: str,
) -> None:
    """Lees JSONL regel voor regel, classificeer en schrijf opnieuw als JSONL."""
    records_read = 0
    records_classified = 0
    records_with_source_category = 0
    records_zero_shot = 0
    records_without_text = 0
    records_with_errors = 0
    records_with_ai_locations = 0
    source_category_distribution = Counter()
    ai_label_distribution = Counter()
    location_distribution = Counter()
    classifier = None

    try:
        with input_file.open("r", encoding="utf-8") as source, output_file.open(
            "w",
            encoding="utf-8",
        ) as target:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue

                if MAX_RECORDS is not None and records_read >= MAX_RECORDS:
                    logging.info("MAX_RECORDS bereikt: %s", MAX_RECORDS)
                    break

                records_read += 1

                try:
                    record = json.loads(line)
                    source_category = get_source_category(record)
                    if source_category:
                        records_with_source_category += 1
                        source_category_distribution[source_category] += 1

                    # Laad het model pas bij het eerste tekstrecord.
                    if classifier is None and combine_ai_input(record):
                        logging.info("AI-model laden: %s", AI_MODEL)
                        classifier = pipeline("zero-shot-classification", model=AI_MODEL)

                    classified_record, status = classify_record(
                        record,
                        classifier,
                        source_dataset_version,
                        ai_output_version,
                    )

                    if status == "zero_shot":
                        records_classified += 1
                        records_zero_shot += 1
                    elif status == "no_text":
                        records_without_text += 1
                    else:
                        records_with_errors += 1

                    detected_locations = classified_record.get("ai_detected_locations", [])
                    if detected_locations:
                        records_with_ai_locations += 1
                        location_distribution.update(detected_locations)

                    ai_label_distribution[classified_record.get("ai_label", "onbekend")] += 1
                    target.write(json.dumps(classified_record, ensure_ascii=False) + "\n")
                except Exception as error:
                    logging.warning("Record op regel %s kon niet worden verwerkt: %s", line_number, error)

    except Exception as error:
        logging.error("AI-verwerking kon niet worden uitgevoerd: %s", error)
        return

    logging.info("Aantal records ingelezen: %s", records_read)
    logging.info("Aantal records geclassificeerd: %s", records_classified)
    logging.info("Aantal records met source_category: %s", records_with_source_category)
    logging.info("Aantal records door zero-shot verwerkt: %s", records_zero_shot)
    logging.info("Aantal records zonder bruikbare tekst: %s", records_without_text)
    logging.info("Aantal records met classificatiefout: %s", records_with_errors)
    logging.info("Totaal aantal records waarin locaties zijn herkend: %s", records_with_ai_locations)
    logging.info("Top 10 meest gevonden locaties: %s", location_distribution.most_common(10))
    logging.info("Labelverdeling source_category: %s", dict(source_category_distribution))
    logging.info("Labelverdeling ai_label: %s", dict(ai_label_distribution))
    logging.info("AI-output opgeslagen in: %s", output_file)


def main() -> None:
    """Startpunt van de AI-classificatiestap."""
    setup_logging()

    try:
        input_file, source_dataset_version = get_latest_scraper_output()
        output_file, ai_output_version = get_next_ai_output_file()
    except Exception as error:
        logging.error("Bestandsversies konden niet worden bepaald: %s", error)
        return

    process_jsonl_file(
        input_file=input_file,
        output_file=output_file,
        source_dataset_version=source_dataset_version,
        ai_output_version=ai_output_version,
    )


if __name__ == "__main__":
    main()
