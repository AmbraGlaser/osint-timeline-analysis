"""Scraper voor openbare OSINT-bronnen met JSONL-output voor ELK Stack."""

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from config import TARGET

try:
    import feedparser
except ImportError:
    # Nieuws-RSS is optioneel; zonder feedparser loopt de rest door.
    feedparser = None

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:
    # Playwright is optioneel; zonder browserinstallatie loopt de rest door.
    PlaywrightTimeoutError = TimeoutError
    sync_playwright = None

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
    )
except ImportError:
    # Transcripties zijn optioneel; YouTube RSS blijft werken zonder deze library.
    YouTubeTranscriptApi = None
    NoTranscriptFound = Exception
    TranscriptsDisabled = Exception
    VideoUnavailable = Exception


# Projectpaden.
BASE_DIR = Path(__file__).resolve().parent
URLS_FILE = BASE_DIR / "urls.txt"
OUTPUT_DIR = BASE_DIR / "output"
MAX_PROFILE_SCROLLS = 3
OUTPUT_VERSION_PATTERN = re.compile(r"^osint_results_v(\d+)\.jsonl$")

# Profiel-URL's; YouTube loopt via RSS.
PLATFORM_URL_TEMPLATES = {
    "instagram": "https://www.instagram.com/{username}/",
    "x": "https://x.com/{username}",
    "tiktok": "https://www.tiktok.com/@{username}",
}

# YouTube RSS-feed.
YOUTUBE_RSS_URL_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

# Namespaces voor YouTube RSS.
RSS_NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}

# Alleen detectie; er wordt niets aangeklikt.
LOGIN_KEYWORDS = [
    "log in",
    "login",
    "sign in",
    "aanmelden",
    "inloggen",
    "register",
    "registreren",
    "sign up",
    "join",
    "create account",
    "account maken",
]
CONSENT_KEYWORDS = [
    "cookie",
    "cookies",
    "consent",
    "accept all",
    "allow all",
    "alle cookies",
    "toestaan",
]

# Hashtags, inclusief accenttekens.
HASHTAG_REGEX = re.compile(r"(?<!\w)#([A-Za-z0-9_À-ÿ-]+)")

# Nederlandse plaatsnamen voor mogelijke locatieherkenning.
DUTCH_PLACE_NAMES = [
    "Amsterdam",
    "Rotterdam",
    "Den Haag",
    "Utrecht",
    "Eindhoven",
    "Groningen",
    "Tilburg",
    "Almere",
    "Breda",
    "Nijmegen",
    "Enschede",
    "Haarlem",
    "Arnhem",
    "Zaanstad",
    "Amersfoort",
    "Apeldoorn",
    "Den Bosch",
    "Hoofddorp",
    "Maastricht",
    "Leiden",
    "Dordrecht",
    "Zoetermeer",
    "Zwolle",
    "Delft",
    "Leeuwarden",
    "Venlo",
    "Deventer",
    "Alkmaar",
    "Hilversum",
    "Assen",
    "Middelburg",
    "Lelystad",
    "Scheveningen",
    "Volendam",
    "Urk",
]


def setup_logging() -> None:
    """Stel logging in zodat zichtbaar is welke URL wel of niet lukt."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def now_utc_iso() -> str:
    """Geef het huidige tijdstip terug in ISO-formaat met UTC-tijdzone."""
    return datetime.now(timezone.utc).isoformat()


def read_urls(file_path: Path) -> List[str]:
    """Lees URL's uit urls.txt en negeer lege regels of commentaarregels."""
    try:
        urls = []
        for line in file_path.read_text(encoding="utf-8").splitlines():
            clean_line = line.strip()
            if clean_line and not clean_line.startswith("#"):
                urls.append(clean_line)
        return urls
    except FileNotFoundError:
        logging.error("Bestand met URL's niet gevonden: %s", file_path)
        return []
    except Exception as error:
        logging.error("Fout bij het lezen van URL's: %s", error)
        return []


def get_next_output_file(output_dir: Path) -> Path:
    """
    Bepaal automatisch de volgende JSONL-outputversie.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    highest_version = 0

    for file_path in output_dir.glob("osint_results_v*.jsonl"):
        match = OUTPUT_VERSION_PATTERN.match(file_path.name)
        if match:
            highest_version = max(highest_version, int(match.group(1)))

    next_version = highest_version + 1
    output_file = output_dir / f"osint_results_v{next_version}.jsonl"
    logging.info("Gekozen outputbestand: %s", output_file.name)
    return output_file


def get_scrape_version_from_output_file(output_file: Path) -> str:
    """Haal de scrapeversie uit de gekozen bestandsnaam, bijvoorbeeld v4."""
    match = OUTPUT_VERSION_PATTERN.match(output_file.name)
    if match:
        return f"v{match.group(1)}"
    return "unknown"


def apply_scrape_version(records: List[Dict[str, object]], scrape_version: str) -> List[Dict[str, object]]:
    """Vul de scrapeversie in elk record zodat herkomst later duidelijk blijft."""
    for record in records:
        record["scrape_version"] = scrape_version
    return records


def count_records_for_platform(records: List[Dict[str, object]], platform: str) -> int:
    """Tel hoeveel records voor een platform zijn gemaakt."""
    return sum(1 for record in records if record.get("platform") == platform)


def build_target_urls(target: Dict[str, str]) -> List[str]:
    """Bouw openbare profiel-URL's op met de juiste username per platform."""
    urls = []

    for platform, url_template in PLATFORM_URL_TEMPLATES.items():
        username = target.get(platform)
        if username:
            urls.append(url_template.format(username=username))
        else:
            logging.info("Geen username ingesteld voor platform: %s", platform)

    return urls


def build_youtube_rss_url(target: Dict[str, str]) -> Optional[str]:
    """Bouw de openbare YouTube RSS-feed-URL op met de channel_id uit config.py."""
    channel_id = target.get("youtube_channel_id")
    if not channel_id:
        logging.warning("Geen youtube_channel_id ingesteld in config.py.")
        return None

    return YOUTUBE_RSS_URL_TEMPLATE.format(channel_id=channel_id)


def combine_unique_urls(*url_lists: List[str]) -> List[str]:
    """Combineer URL-lijsten en voorkom dubbele verwerking."""
    unique_urls = []
    seen_urls = set()

    for url_list in url_lists:
        for url in url_list:
            if url not in seen_urls:
                unique_urls.append(url)
                seen_urls.add(url)

    return unique_urls


def detect_platform(url: str) -> str:
    """Bepaal het platform op basis van de domeinnaam."""
    domain = urlparse(url).netloc.lower()

    if "youtube.com" in domain or "youtu.be" in domain:
        return "YouTube"
    if "instagram.com" in domain:
        return "Instagram"
    if "tiktok.com" in domain:
        return "TikTok"
    if "x.com" in domain or "twitter.com" in domain:
        return "X"

    return "Onbekend"


def detect_youtube_content_type(source_url: Optional[str]) -> str:
    """Herken YouTube Shorts aan de URL; anders is het een gewone video."""
    if source_url and "/shorts/" in source_url:
        return "short"
    return "video"


def make_record(
    platform: str,
    source_url: str,
    content_type: str,
    title: Optional[str],
    text: Optional[str],
    published_at: Optional[str] = None,
    media_url: Optional[str] = None,
    raw_html_available: bool = False,
    transcript_available: bool = False,
    transcript: Optional[str] = None,
    scrape_method: str = "html_metadata",
) -> Dict[str, object]:
    """Maak een compleet JSONL-record met alle vaste velden voor ELK Stack."""
    combined_text = " ".join(part for part in [title, text, transcript] if part)

    return {
        "platform": platform,
        "source_url": source_url,
        "scraped_at": now_utc_iso(),
        "published_at": parse_date(published_at),
        "title": title,
        "text": text,
        "hashtags": extract_hashtags(combined_text),
        "possible_locations": extract_locations(combined_text),
        "media_url": media_url,
        "raw_html_available": raw_html_available,
        "content_type": content_type,
        "transcript_available": transcript_available,
        "transcript": transcript,
        "scrape_version": None,
        "scrape_method": scrape_method,
    }


def fetch_html(url: str) -> Optional[str]:
    """
    Haal HTML op van een openbare pagina.
    Als een platform openbare scraping blokkeert, wordt dat alleen gelogd.
    """
    headers = {
        "User-Agent": "OSINT-praktijkonderzoek/1.0 (+alleen openbare pagina's; geen login)",
        "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
    }

    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        logging.info("Succesvol verwerkt: %s", url)
        return response.text
    except requests.exceptions.RequestException as error:
        logging.warning("Niet succesvol verwerkt: %s | reden: %s", url, error)
        return None
    except Exception as error:
        logging.warning("Onverwachte fout bij URL: %s | reden: %s", url, error)
        return None


def fetch_text(url: str) -> Optional[str]:
    """
    Haal tekstuele data op, zoals een openbare RSS-feed.
    """
    headers = {
        "User-Agent": "OSINT-praktijkonderzoek/1.0 (+openbare RSS-feed; geen login)",
        "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
    }

    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        logging.info("Succesvol opgehaald: %s", url)
        return response.text
    except requests.exceptions.RequestException as error:
        logging.warning("RSS-feed niet bereikbaar: %s | reden: %s", url, error)
        return None
    except Exception as error:
        logging.warning("Onverwachte fout bij ophalen van RSS-feed: %s | reden: %s", url, error)
        return None


def get_meta_content(soup: BeautifulSoup, *names: str) -> Optional[str]:
    """Zoek in meerdere soorten metadata naar de eerste beschikbare waarde."""
    for name in names:
        tag = soup.find("meta", attrs={"property": name})
        if tag and tag.get("content"):
            return tag["content"].strip()

        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()

        tag = soup.find("meta", attrs={"itemprop": name})
        if tag and tag.get("content"):
            return tag["content"].strip()

    return None


def parse_date(value: Optional[str]) -> Optional[str]:
    """Zet een gevonden datum om naar een consistente notatie."""
    if not value:
        return None

    try:
        parsed_date = date_parser.parse(value)
        if parsed_date.tzinfo is None:
            parsed_date = parsed_date.replace(tzinfo=timezone.utc)
        return parsed_date.astimezone(timezone.utc).isoformat()
    except Exception:
        return value


def extract_page_text(soup: BeautifulSoup) -> str:
    """Maak een eenvoudige tekstweergave van de openbare HTML-pagina."""
    for unwanted in soup(["script", "style", "noscript"]):
        unwanted.decompose()

    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text)


def extract_hashtags(text: str) -> List[str]:
    """Haal unieke hashtags uit tekst met een eenvoudige regex."""
    hashtags = HASHTAG_REGEX.findall(text or "")
    return sorted({f"#{hashtag}" for hashtag in hashtags}, key=str.lower)


def extract_locations(text: str) -> List[str]:
    """Zoek Nederlandse plaatsnamen in de tekst met woordgrenzen."""
    found_locations = []

    for place_name in DUTCH_PLACE_NAMES:
        pattern = rf"\b{re.escape(place_name)}\b"
        if re.search(pattern, text or "", flags=re.IGNORECASE):
            found_locations.append(place_name)

    return sorted(set(found_locations), key=str.lower)


def text_contains_keywords(text: str, keywords: List[str]) -> bool:
    """Controleer eenvoudig of login- of consentwoorden zichtbaar zijn."""
    lower_text = (text or "").lower()
    return any(keyword in lower_text for keyword in keywords)


def first_locator_attribute(locator, attribute_name: str) -> Optional[str]:
    """Lees veilig een attribuut uit de eerste match van een Playwright locator."""
    try:
        if locator.count() == 0:
            return None
        return locator.first.get_attribute(attribute_name, timeout=1000)
    except Exception:
        return None


def first_locator_text(locator) -> Optional[str]:
    """Lees veilig zichtbare tekst uit de eerste match van een Playwright locator."""
    try:
        if locator.count() == 0:
            return None
        return locator.first.inner_text(timeout=1000).strip()
    except Exception:
        return None


def make_dynamic_record(
    platform: str,
    source_url: str,
    content_type: str,
    title: Optional[str],
    text: Optional[str],
    published_at: Optional[str] = None,
    media_url: Optional[str] = None,
    raw_html_available: bool = True,
    scrape_method: str = "playwright_public_page",
) -> Dict[str, object]:
    """Maak een record voor content die via Playwright zichtbaar was."""
    return make_record(
        platform=platform,
        source_url=source_url,
        content_type=content_type,
        title=title,
        text=text,
        published_at=published_at,
        media_url=media_url,
        raw_html_available=raw_html_available,
        scrape_method=scrape_method,
    )


def make_profile_record_from_page(page, url: str, platform: str, visible_text: str) -> Dict[str, object]:
    """Maak een profielrecord van de zichtbaar geladen profielpagina."""
    title = page.title()
    media_url = first_locator_attribute(page.locator("meta[property='og:image']"), "content")

    return make_dynamic_record(
        platform=platform,
        source_url=url,
        content_type="profile",
        title=title,
        text=visible_text[:3000] if visible_text else None,
        media_url=media_url,
        raw_html_available=True,
    )


def extract_published_at(soup: BeautifulSoup) -> Optional[str]:
    """Probeer de publicatiedatum uit bekende metadata-velden te halen."""
    published_at = get_meta_content(
        soup,
        "article:published_time",
        "video:release_date",
        "datePublished",
        "uploadDate",
        "pubdate",
        "publishdate",
    )

    if not published_at:
        time_tag = soup.find("time")
        if time_tag:
            published_at = time_tag.get("datetime") or time_tag.get_text(strip=True)

    return parse_date(published_at)


def get_xml_text(element: ET.Element, path: str) -> Optional[str]:
    """Lees veilig tekst uit een XML-element met namespaces."""
    found = element.find(path, RSS_NAMESPACES)
    if found is not None and found.text:
        return found.text.strip()
    return None


def fetch_youtube_transcript(video_id: Optional[str]) -> Optional[str]:
    """
    Haal optioneel een openbaar beschikbare YouTube-transcriptie op.
    Als er geen transcript is, logt het script dit en gaat het verder met de volgende video.
    """
    if not video_id:
        return None

    if YouTubeTranscriptApi is None:
        logging.warning("youtube-transcript-api is niet geinstalleerd; transcripties worden overgeslagen.")
        return None

    try:
        try:
            transcript_items = YouTubeTranscriptApi.get_transcript(video_id, languages=["nl", "en"])
        except AttributeError:
            fetched = YouTubeTranscriptApi().fetch(video_id, languages=["nl", "en"])
            transcript_items = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else fetched

        transcript_text = " ".join(
            item.get("text", "").replace("\n", " ").strip()
            for item in transcript_items
            if item.get("text")
        )
        return re.sub(r"\s+", " ", transcript_text).strip() or None
    except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable) as error:
        logging.info("Geen YouTube-transcript beschikbaar voor %s: %s", video_id, error)
        return None
    except Exception as error:
        logging.warning("Transcript ophalen mislukt voor %s: %s", video_id, error)
        return None


def parse_youtube_rss(feed_url: str, rss_text: str) -> List[Dict[str, object]]:
    """Lees de YouTube RSS-feed en maak een record per video."""
    records = []
    transcript_count = 0

    try:
        root = ET.fromstring(rss_text)
    except ET.ParseError as error:
        logging.warning("YouTube RSS-feed kon niet worden geparsed: %s", error)
        return records

    entries = root.findall("atom:entry", RSS_NAMESPACES)
    logging.info("Aantal YouTube-video's gevonden via RSS: %s", len(entries))

    for entry in entries:
        title = get_xml_text(entry, "atom:title")
        description = get_xml_text(entry, "media:group/media:description")
        published_at = parse_date(get_xml_text(entry, "atom:published"))
        video_id = get_xml_text(entry, "yt:videoId")

        # Videolink uit de RSS-entry.
        link_element = entry.find("atom:link[@rel='alternate']", RSS_NAMESPACES)
        source_url = link_element.get("href") if link_element is not None else None
        if not source_url and video_id:
            source_url = f"https://www.youtube.com/watch?v={video_id}"

        # Thumbnail voor media_url.
        thumbnail_element = entry.find("media:group/media:thumbnail", RSS_NAMESPACES)
        media_url = thumbnail_element.get("url") if thumbnail_element is not None else None
        transcript = fetch_youtube_transcript(video_id)
        transcript_available = bool(transcript)
        if transcript_available:
            transcript_count += 1

        # Transcriptie wordt aan text toegevoegd.
        text_parts = [description, transcript] if transcript else [description]
        text = "\n\n".join(part for part in text_parts if part)

        records.append(
            make_record(
                platform="YouTube",
                source_url=source_url or feed_url,
                content_type=detect_youtube_content_type(source_url),
                title=title,
                text=text,
                published_at=published_at,
                media_url=media_url,
                raw_html_available=False,
                transcript_available=transcript_available,
                transcript=transcript,
                scrape_method="youtube_transcript" if transcript_available else "youtube_rss",
            )
        )

    logging.info("Aantal gevonden YouTube-transcripties: %s", transcript_count)
    return records


def scrape_youtube_videos(target: Dict[str, str]) -> List[Dict[str, object]]:
    """Haal YouTube-video's op via de openbare RSS-feed."""
    feed_url = build_youtube_rss_url(target)
    if not feed_url:
        return []

    rss_text = fetch_text(feed_url)
    if not rss_text:
        return [make_error_record(feed_url, platform="YouTube", content_type="video")]

    return parse_youtube_rss(feed_url, rss_text)


def clean_news_summary(summary: Optional[str]) -> Optional[str]:
    """Zet de HTML-samenvatting uit een nieuwsfeed om naar leesbare tekst."""
    if not summary:
        return None

    soup = BeautifulSoup(summary, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip() or None


def get_news_media_url(entry) -> Optional[str]:
    """Haal optioneel een media-URL uit een nieuwsfeed-item."""
    media_content = entry.get("media_content")
    if media_content and isinstance(media_content, list):
        return media_content[0].get("url")

    media_thumbnail = entry.get("media_thumbnail")
    if media_thumbnail and isinstance(media_thumbnail, list):
        return media_thumbnail[0].get("url")

    return None


def scrape_news_articles(news_urls: List[str]) -> List[Dict[str, object]]:
    """Lees nieuws-RSS-feeds en maak een record per artikel."""
    records = []
    seen_urls = set()
    seen_titles = set()
    duplicate_count = 0

    if not news_urls:
        logging.info("Geen nieuwsfeeds ingesteld in config.py.")
        return records

    if feedparser is None:
        logging.warning("feedparser is niet geinstalleerd; nieuwsartikelen worden overgeslagen.")
        return records

    for feed_url in news_urls:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as error:
            logging.warning("Nieuwsfeed kon niet worden gelezen: %s | reden: %s", feed_url, error)
            continue

        if getattr(feed, "bozo", False):
            logging.warning("Nieuwsfeed gaf een parse-waarschuwing: %s", feed_url)

        for entry in feed.entries:
            source_url = entry.get("link") or feed_url
            title = entry.get("title")
            title_key = (title or "").strip().lower()
            url_key = (source_url or "").strip().lower()

            if url_key in seen_urls or (title_key and title_key in seen_titles):
                duplicate_count += 1
                continue

            seen_urls.add(url_key)
            if title_key:
                seen_titles.add(title_key)

            published_at = (
                entry.get("published")
                or entry.get("updated")
                or entry.get("created")
            )
            text = clean_news_summary(entry.get("summary") or entry.get("description"))

            records.append(
                make_record(
                    platform="News",
                    source_url=source_url,
                    content_type="article",
                    title=title,
                    text=text,
                    published_at=published_at,
                    media_url=get_news_media_url(entry),
                    raw_html_available=False,
                    scrape_method="news_rss",
                )
            )

    logging.info("Aantal nieuwsartikelen gevonden: %s", len(records))
    logging.info("Aantal dubbele nieuwsartikelen overgeslagen: %s", duplicate_count)

    if not records:
        logging.warning("Nieuwsfeeds leverden geen artikelen op.")

    return records


def extract_instagram_posts(page, profile_url: str) -> List[Dict[str, object]]:
    """Haal zichtbare Instagram-postlinks uit de geladen profielpagina."""
    records = []
    seen_urls = set()

    # Instagram-posts en reels.
    post_links = page.locator("article a[href*='/p/'], article a[href*='/reel/']")

    for index in range(post_links.count()):
        link = post_links.nth(index)
        href = link.get_attribute("href")
        if not href:
            continue

        source_url = urljoin(profile_url, href)
        if source_url in seen_urls:
            continue
        seen_urls.add(source_url)

        image = link.locator("img").first
        image_alt = first_locator_attribute(image, "alt")
        image_url = first_locator_attribute(image, "src")
        link_text = first_locator_text(link)
        text = image_alt or link_text
        title = (text[:120] if text else "Instagram-post")

        records.append(
            make_dynamic_record(
                platform="Instagram",
                source_url=source_url,
                content_type="post",
                title=title,
                text=text,
                media_url=image_url,
            )
        )

    return records


def extract_tiktok_posts(page, profile_url: str) -> List[Dict[str, object]]:
    """Haal zichtbare TikTok-video's uit de geladen openbare profielpagina."""
    records = []
    seen_urls = set()

    # TikTok-video's.
    video_links = page.locator("a[href*='/video/']")

    for index in range(video_links.count()):
        link = video_links.nth(index)
        href = first_locator_attribute(link, "href")
        if not href:
            continue

        source_url = urljoin(profile_url, href)
        if source_url in seen_urls:
            continue
        seen_urls.add(source_url)

        image = link.locator("img").first
        image_alt = first_locator_attribute(image, "alt")
        image_url = first_locator_attribute(image, "src")
        link_text = first_locator_text(link)
        text = image_alt or link_text
        title = text[:120] if text else "TikTok-post"

        records.append(
            make_dynamic_record(
                platform="TikTok",
                source_url=source_url,
                content_type="post",
                title=title,
                text=text,
                media_url=image_url,
            )
        )

    return records


def extract_x_posts(page, profile_url: str) -> List[Dict[str, object]]:
    """Haal alleen zichtbaar geladen X-posts op zonder login of cookies."""
    records = []
    seen_urls = set()

    # X-posts met statuslink.
    articles = page.locator("article")

    for index in range(articles.count()):
        article = articles.nth(index)
        status_link = article.locator("a[href*='/status/']").first
        href = first_locator_attribute(status_link, "href")
        if not href:
            continue

        source_url = urljoin(profile_url, href)
        if source_url in seen_urls:
            continue
        seen_urls.add(source_url)

        # Eerst tweetText, daarna zichtbare article-tekst.
        try:
            text_parts = article.locator("[data-testid='tweetText']").all_inner_texts()
        except Exception:
            text_parts = []

        text = "\n".join(part.strip() for part in text_parts if part.strip())
        if not text:
            text = first_locator_text(article)
        if not text:
            continue

        published_at = first_locator_attribute(article.locator("time").first, "datetime")
        media_url = first_locator_attribute(article.locator("img[src*='twimg.com/media']").first, "src")
        title = text.splitlines()[0][:120] if text.splitlines() else "X-post"

        records.append(
            make_dynamic_record(
                platform="X",
                source_url=source_url,
                content_type="post",
                title=title,
                text=text,
                published_at=published_at,
                media_url=media_url,
            )
        )

    return records


def extract_visible_posts(page, platform: str, profile_url: str) -> List[Dict[str, object]]:
    """Kies de juiste postextractie voor Instagram, X of TikTok."""
    if platform == "Instagram":
        return extract_instagram_posts(page, profile_url)
    if platform == "X":
        return extract_x_posts(page, profile_url)
    if platform == "TikTok":
        return extract_tiktok_posts(page, profile_url)
    return []


def scrape_dynamic_profile(url: str) -> List[Dict[str, object]]:
    """
    Open een openbare Instagram-, X- of TikTok-pagina met Playwright.
    Er wordt maximaal drie keer gescrold om het verzamelen beperkt te houden.
    """
    platform = detect_platform(url)
    if platform not in ["Instagram", "X", "TikTok"]:
        return []

    if sync_playwright is None:
        logging.error("Playwright is niet geinstalleerd. Voer uit: pip install -r requirements.txt")
        return [make_error_record(url, platform=platform, content_type="profile")]

    records = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                locale="nl-NL",
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
            except PlaywrightTimeoutError as error:
                logging.warning("%s laden duurde te lang: %s", platform, error)

            visible_text = page.locator("body").inner_text(timeout=5000)
            login_visible = text_contains_keywords(visible_text, LOGIN_KEYWORDS)
            consent_visible = text_contains_keywords(visible_text, CONSENT_KEYWORDS)
            logging.info(
                "%s login/registratie zichtbaar: %s | consent/cookies zichtbaar: %s",
                platform,
                login_visible,
                consent_visible,
            )
            if platform == "X":
                logging.warning("X levert zonder login/API beperkt resultaat op.")

            # Beperkte scroll voor openbaar zichtbare posts.
            for scroll_number in range(MAX_PROFILE_SCROLLS):
                page.mouse.wheel(0, 1800)
                page.wait_for_timeout(1500)
                logging.info("%s scroll %s van %s uitgevoerd", platform, scroll_number + 1, MAX_PROFILE_SCROLLS)

            post_records = extract_visible_posts(page, platform, url)
            logging.info("%s zichtbare posts gevonden: %s", platform, len(post_records))

            if platform == "Instagram":
                logging.info("Aantal gevonden Instagram-posts: %s", len(post_records))
            if platform == "X":
                logging.info("Aantal gevonden X-posts: %s", len(post_records))
            if platform == "TikTok":
                logging.info("Aantal gevonden TikTok-posts: %s", len(post_records))

            if platform == "X" and not post_records:
                profile_record = make_profile_record_from_page(page, url, platform, visible_text)
                profile_record["text"] = (
                    (profile_record.get("text") or "")
                    + "\n\nOnderzoeksbevinding: X levert zonder login/API beperkt resultaat op."
                ).strip()
                records.append(profile_record)
            else:
                # Bewaar profielinformatie naast zichtbare posts.
                records.append(make_profile_record_from_page(page, url, platform, visible_text))

            if not post_records and platform != "X":
                if platform == "TikTok":
                    logging.warning("TikTok beperkt bruikbaar zonder login/API; geen losse posts gevonden.")
                else:
                    logging.warning("%s toont geen openbare posts zonder login of na beperkte scrolls.", platform)

            records.extend(post_records)

            context.close()
            browser.close()

    except Exception as error:
        logging.warning("Playwright-verwerking mislukt voor %s: %s", url, error)
        return [make_error_record(url, platform=platform, content_type="profile")]

    return records


def scrape_dynamic_profiles(urls: List[str]) -> List[Dict[str, object]]:
    """Verwerk alle Instagram- en TikTok-URL's met Playwright."""
    records = []

    for url in urls:
        records.extend(scrape_dynamic_profile(url))

    return records


def parse_html(url: str, html: str, content_type: str = "profile") -> Dict[str, object]:
    """Lees de gevraagde velden uit de opgehaalde HTML."""
    soup = BeautifulSoup(html, "html.parser")

    title = (
        get_meta_content(soup, "og:title", "twitter:title")
        or (soup.title.get_text(strip=True) if soup.title else "")
    )
    description = get_meta_content(
        soup,
        "og:description",
        "twitter:description",
        "description",
    )
    page_text = extract_page_text(soup)
    combined_text = " ".join(part for part in [title, description, page_text] if part)

    return make_record(
        platform=detect_platform(url),
        source_url=url,
        content_type=content_type,
        title=title,
        text=description or page_text[:3000],
        published_at=extract_published_at(soup),
        media_url=get_meta_content(soup, "og:image", "twitter:image", "og:video"),
        raw_html_available=True,
        scrape_method="html_metadata",
    )


def make_error_record(
    url: str,
    platform: Optional[str] = None,
    content_type: str = "profile",
) -> Dict[str, object]:
    """Maak ook bij mislukte verwerking een record voor transparantie."""
    return make_record(
        platform=platform or detect_platform(url),
        source_url=url,
        content_type=content_type,
        title=None,
        text=None,
        raw_html_available=False,
        scrape_method="html_metadata",
    )


def write_jsonl(records: List[Dict[str, object]], output_file: Path) -> None:
    """Schrijf records als JSONL: een JSON-object per regel."""
    try:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with output_file.open("w", encoding="utf-8") as file:
            for record in records:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        logging.info("Resultaten opgeslagen in: %s", output_file)
        logging.info("Aantal records opgeslagen: %s", len(records))
    except Exception as error:
        logging.error("Fout bij het opslaan van resultaten: %s", error)


def scrape_urls(urls: List[str]) -> List[Dict[str, object]]:
    """Verwerk alle URL's en verzamel succesvolle en mislukte records."""
    records = []

    for url in urls:
        html = fetch_html(url)
        if html:
            try:
                records.append(parse_html(url, html))
            except Exception as error:
                logging.warning("HTML opgehaald, maar parseren mislukt voor %s: %s", url, error)
                records.append(make_error_record(url))
        else:
            records.append(make_error_record(url))

    return records


def main() -> None:
    """Startpunt van het script."""
    setup_logging()

    logging.info("Onderzoeksobject: %s", TARGET.get("name", "Onbekend"))
    output_file = get_next_output_file(OUTPUT_DIR)
    scrape_version = get_scrape_version_from_output_file(output_file)

    # YouTube via RSS.
    youtube_records = scrape_youtube_videos(TARGET)

    # Nieuws via RSS.
    news_records = scrape_news_articles(TARGET.get("news_queries", []))

    # Profiel-URL's uit config.py.
    target_urls = build_target_urls(TARGET)

    # Extra URL's uit urls.txt.
    extra_urls = read_urls(URLS_FILE)

    # Dynamische platformen via Playwright.
    extra_instagram_urls = [url for url in extra_urls if detect_platform(url) == "Instagram"]
    extra_x_urls = [url for url in extra_urls if detect_platform(url) == "X"]
    extra_tiktok_urls = [url for url in extra_urls if detect_platform(url) == "TikTok"]
    dynamic_urls = combine_unique_urls(
        target_urls,
        extra_instagram_urls,
        extra_x_urls,
        extra_tiktok_urls,
    )

    # Overige URL's via requests/BeautifulSoup.
    static_urls = [
        url for url in extra_urls if detect_platform(url) not in ["Instagram", "X", "TikTok"]
    ]

    if not dynamic_urls and not static_urls and not youtube_records and not news_records:
        logging.warning("Geen bronnen gevonden. Vul config.py of urls.txt met openbare bronnen.")
        return

    dynamic_records = scrape_dynamic_profiles(dynamic_urls)
    static_records = scrape_urls(static_urls)
    records = youtube_records + news_records + dynamic_records + static_records
    records = apply_scrape_version(records, scrape_version)

    logging.info("Aantal Instagram-records: %s", count_records_for_platform(records, "Instagram"))
    logging.info("Aantal X-records: %s", count_records_for_platform(records, "X"))
    logging.info("Aantal TikTok-records: %s", count_records_for_platform(records, "TikTok"))
    logging.info("Aantal News-records: %s", count_records_for_platform(records, "News"))

    write_jsonl(records, output_file)


if __name__ == "__main__":
    main()
