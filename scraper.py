"""
Monitor annunci affitti - Vlore (Albania) - FILTRO 2 camere + 2 bagni
=======================================================================

Versione specializzata: monitora SOLO Homezone.al (l'unico dei tre siti che
mostra chiaramente il numero di camere da letto e di bagni per ogni
annuncio), e tiene solo gli annunci con ESATTAMENTE 2 camere da letto
("Dhoma") e 2 bagni ("Banjo").

Nota tecnica: su Homezone.al, ogni scheda annuncio e' un unico blocco
cliccabile che contiene tutto il testo (prezzo, camere, bagni, indirizzo).
Per questo leggiamo tutto il testo dentro il link e cerchiamo con un
pattern (regex) i numeri accanto a "Dhoma" (camere) e "Banjo" (bagni).

Non serve capire il codice per usarlo: basta seguire il README.
"""

import json
import os
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# CONFIGURAZIONE
# ---------------------------------------------------------------------------

SEEN_FILE = Path("seen.json")

SITES = {
    "homezone": {
        "url": "https://homezone.al/properties/rent/vlore",
        "label": "Homezone.al",
    },
}

# Filtro: quante camere da letto e quanti bagni deve avere l'annuncio
FILTER_BEDROOMS = 2
FILTER_BATHROOMS = 2

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "sq,it;q=0.9,en;q=0.8",
}

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# ---------------------------------------------------------------------------
# FUNZIONI
# ---------------------------------------------------------------------------

def load_seen():
    """Legge il file con gli annunci gia' visti (se esiste)."""
    if SEEN_FILE.exists():
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for site_key, value in data.items():
            if isinstance(value, list):
                data[site_key] = {listing_id: "" for listing_id in value}
        return data
    return {site_key: {} for site_key in SITES}


def save_seen(seen):
    """Salva il file con gli annunci visti, per la prossima esecuzione."""
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


def extract_room_counts(card_text):
    """
    Cerca nel testo della scheda quante camere da letto (Dhoma) e quanti
    bagni (Banjo) ha l'annuncio. Ritorna (camere, bagni) oppure (None, None)
    se non li trova (in tal caso l'annuncio viene scartato, per sicurezza:
    meglio perdere un annuncio dubbio che mostrarne uno sbagliato).
    """
    bedroom_match = re.search(r"(\d+)\s*Dhom", card_text)
    bathroom_match = re.search(r"(\d+)\s*Banjo", card_text)

    bedrooms = int(bedroom_match.group(1)) if bedroom_match else None
    bathrooms = int(bathroom_match.group(1)) if bathroom_match else None
    return bedrooms, bathrooms


def extract_price(card_text):
    """Cerca un prezzo nel testo (es. '€500.00' o 'LEK 60000'), solo per
    rendere piu' leggibile il messaggio Telegram. Non e' usato per filtrare."""
    match = re.search(r"(€\s?[\d.,]+|LEK\s?[\d.,]+)", card_text)
    return match.group(1).strip() if match else ""


def extract_listings(html, base_url):
    """
    Estrae gli annunci dalla pagina, applicando subito il filtro
    2 camere + 2 bagni.
    """
    soup = BeautifulSoup(html, "html.parser")
    listings = {}

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]

        if "/property/rent/" not in href:
            continue
        if not re.search(r"-\d{4,}$", href.split("?")[0]):
            continue

        if href.startswith("/"):
            full_url = base_url.split("/")[0] + "//" + base_url.split("/")[2] + href
        elif href.startswith("http"):
            full_url = href
        else:
            continue

        listing_id = full_url.split("?")[0].rstrip("/")

        card_text = a_tag.get_text(" ", strip=True)
        if not card_text:
            continue

        bedrooms, bathrooms = extract_room_counts(card_text)
        if bedrooms != FILTER_BEDROOMS or bathrooms != FILTER_BATHROOMS:
            continue  # non corrisponde al filtro, saltiamo

        price = extract_price(card_text)
        # Costruiamo un titolo leggibile per la notifica
        title_parts = [p for p in [price, f"{bedrooms} camere", f"{bathrooms} bagni"] if p]
        title = " · ".join(title_parts) if title_parts else card_text[:80]

        if listing_id not in listings:
            listings[listing_id] = title

    return listings


def fetch_site(site_key, site_info):
    try:
        resp = requests.get(site_info["url"], headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"[ATTENZIONE] Non sono riuscito a leggere {site_info['label']}: {e}")
        return {}

    return extract_listings(resp.text, site_info["url"])


def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[ATTENZIONE] TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID non configurati, salto invio.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, data=payload, timeout=15)
        if not r.ok:
            print(f"[ATTENZIONE] Telegram ha risposto con errore: {r.text}")
    except Exception as e:
        print(f"[ATTENZIONE] Invio Telegram fallito: {e}")


# ---------------------------------------------------------------------------
# PROGRAMMA PRINCIPALE
# ---------------------------------------------------------------------------

def main():
    seen = load_seen()
    site_keys = list(SITES.keys())
    is_first_run = all(len(seen.get(k, {})) == 0 for k in site_keys)

    seen = {k: v for k, v in seen.items() if k in site_keys}

    total_new = 0

    for site_key, site_info in SITES.items():
        current_listings = fetch_site(site_key, site_info)
        already_seen_ids = set(seen.get(site_key, {}).keys())

        new_ids = [lid for lid in current_listings if lid not in already_seen_ids]

        print(f"{site_info['label']}: trovati {len(current_listings)} annunci "
              f"{FILTER_BEDROOMS} camere + {FILTER_BATHROOMS} bagni, di cui {len(new_ids)} nuovi.")

        if not is_first_run:
            for listing_id in new_ids:
                title = current_listings[listing_id]
                message = (
                    f"🏠 <b>Nuovo annuncio {FILTER_BEDROOMS} camere/{FILTER_BATHROOMS} bagni - {site_info['label']}</b>\n"
                    f"{title}\n"
                    f"{listing_id}"
                )
                send_telegram_message(message)
                total_new += 1
                time.sleep(1)

        seen[site_key] = current_listings

    seen["_meta"] = {
        "last_run": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "site_labels": {k: v["label"] for k, v in SITES.items()},
    }

    if is_first_run:
        print("Prima esecuzione: ho salvato gli annunci attuali come 'gia' visti', "
              "senza inviare notifiche.")
        send_telegram_message(
            f"✅ Il monitor annunci {FILTER_BEDROOMS} camere/{FILTER_BATHROOMS} bagni Vlore e' attivo.\n"
            "Ho salvato gli annunci attualmente online. "
            "Da domani ti avviso solo per i NUOVI annunci corrispondenti."
        )
    else:
        print(f"Totale nuovi annunci inviati: {total_new}")

    save_seen(seen)


if __name__ == "__main__":
    main()
