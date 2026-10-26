"""Recoge titulares de los feeds, los clasifica y escribe public/data.json.

No redacta nada: titulares y extractos se guardan tal como los publica cada medio.
Uso: python scripts/update.py
"""
import calendar
import os
import datetime as dt
import hashlib
import html
import json
import pathlib
import re
import time
import unicodedata
import urllib.parse
import urllib.request

import feedparser

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = json.loads(pathlib.Path(os.environ.get("CONFIG", ROOT / "scripts" / "config.json")).read_text(encoding="utf-8"))
OUT = pathlib.Path(os.environ.get("OUT", ROOT / "public" / "data.json"))
UA = "Mozilla/5.0 (compatible; DondeEstaKeiko/1.0)"
NOW = dt.datetime.now(dt.timezone.utc)


def norm(s):
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def has_word(text, phrase):
    return re.search(r"(?<!\w)" + re.escape(norm(phrase)) + r"(?!\w)", text) is not None


def excerpt(s, n):
    words = s.split()
    return " ".join(words[:n]) + ("…" if len(words) > n else "")


def is_about(t):
    a = CFG["about"]
    if any(has_word(t, w) for w in a["any"]):
        return True
    return has_word(t, "fujimori") and any(has_word(t, w) for w in a["with_fujimori"])


def classify(t):
    for key in CFG["order"]:
        if any(has_word(t, w) for w in CFG["states"][key]["words"]):
            return key
    return "posando"


PLACES = {norm(k): (k, v) for k, v in CFG["places"].items()}
ABROAD = {norm(k): k for k in CFG["abroad"]}
ALL_PLACES = sorted(list(PLACES) + list(ABROAD), key=len, reverse=True)
FILLER = r"(?:la ciudad de |la region |el departamento de |la provincia de )?"


def find_place(t, verbs):
    for v in sorted(verbs, key=len, reverse=True):
        for p in ALL_PLACES:
            if re.search(r"(?<!\w)" + re.escape(norm(v)) + r"\s+" + FILLER + re.escape(p) + r"(?!\w)", t):
                if p in PLACES:
                    name, (lat, lng) = PLACES[p]
                    return {"name": name, "lat": lat, "lng": lng, "abroad": False}
                return {"name": ABROAD[p], "abroad": True}
    return None


VENUE_TYPES = sorted(CFG.get("venue_types", []), key=len, reverse=True)
CAP = r"[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñü.'-]*"
VENUE_RE = re.compile(
    r"(?<!\w)(?:en|desde|visitó|visita|llegó a|llega a|recorrió|recorre)\s+(?:el |la |los |las |al )?"
    r"((?:" + "|".join(re.escape(v) for v in VENUE_TYPES) + r")(?:\s+(?:de|del|la|las|los|el|y|" + CAP + r"))*)"
) if VENUE_TYPES else None
GEOCACHE = {}
GEO_BUDGET = [25]  # consultas nuevas por pasada, para respetar el servicio gratuito


def geocode(query):
    key = norm(query)
    if key in GEOCACHE:
        return GEOCACHE[key]
    if GEO_BUDGET[0] <= 0:
        return None
    GEO_BUDGET[0] -= 1
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": query, "format": "json", "limit": 1, "countrycodes": "pe"})
    result = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DondeEstaKeiko/1.0 (github.com/manrayx/dondekeiko)"})
        with urllib.request.urlopen(req, timeout=15) as r:
            rows = json.loads(r.read().decode())
        if rows:
            result = [round(float(rows[0]["lat"]), 5), round(float(rows[0]["lon"]), 5)]
    except Exception as ex:  # noqa: BLE001
        print(f"Geocodificación falló para {query}: {ex}")
        return None
    time.sleep(1.1)
    GEOCACHE[key] = result
    return result


def find_venue(raw):
    if not VENUE_RE:
        return None
    m = VENUE_RE.search(raw)
    if not m:
        return None
    words = m.group(1).split()
    while words and words[-1].lower() in ("de", "del", "la", "las", "los", "el", "y"):
        words.pop()
    name = " ".join(words).rstrip(".,")
    if name == "Palacio":
        name = "Palacio de Gobierno"
    return name if len(name.split()) >= 2 or name in ("Congreso", "Vaticano") else None


def locate(title, summary):
    """Lugar donde estuvo: el sitio exacto (santuario, plaza…) si la nota lo nombra; si no, la ciudad."""
    for raw in (title, summary):
        if not raw:
            continue
        t = norm(raw)
        city = find_place(t, CFG["here_verbs"]) or find_place(t, ["en", "de"])
        venue = find_venue(raw)
        if venue:
            if city and city.get("abroad"):
                return {"name": venue, "city": city["name"], "abroad": True}
            extra = ", " + city["name"] if city and norm(city["name"]) not in norm(venue) else ""
            coords = geocode(venue + extra + ", Perú")
            base = city or {"name": None, "lat": None, "lng": None}
            if coords:
                return {"name": venue, "city": base["name"], "lat": coords[0], "lng": coords[1], "abroad": False}
            if city:
                return {"name": venue, "city": city["name"], "lat": city["lat"], "lng": city["lng"], "abroad": False}
        here = find_place(t, CFG["here_verbs"])
        if here:
            return {**here, "city": here["name"]}
    return None


def entry_date(e):
    for k in ("published_parsed", "updated_parsed"):
        if e.get(k):
            return dt.datetime.fromtimestamp(calendar.timegm(e[k]), dt.timezone.utc)
    return None


LIMA_TZ = dt.timezone(dt.timedelta(hours=-5))


def gazetteer(text):
    t = norm(text)
    if "palacio de gobierno" in t or "cercado de lima" in t:
        name, (lat, lng) = PLACES["lima"]
        return {"name": name, "lat": lat, "lng": lng, "abroad": False}
    for p in ALL_PLACES:
        if p in PLACES and has_word(t, p):
            name, (lat, lng) = PLACES[p]
            return {"name": name, "lat": lat, "lng": lng, "abroad": False}
    return None


def agenda_place(lugar):
    """Lugar de la agenda oficial: el texto tal cual y coordenadas por geocodificación o por ciudad conocida."""
    if not lugar:
        return None
    parts = [x.strip() for x in lugar.split(",") if x.strip()]
    name = parts[0]
    city = gazetteer(lugar)
    coords = geocode(lugar + ", Perú")
    if not coords and len(parts) > 1:
        coords = geocode(parts[0] + ", " + parts[-1] + ", Perú")
    if coords:
        return {"name": name, "full": lugar, "city": city["name"] if city else None,
                "lat": coords[0], "lng": coords[1], "abroad": False}
    if city:
        return {"name": name, "full": lugar, "city": city["name"], "lat": city["lat"], "lng": city["lng"], "abroad": False}
    d = CFG["places"][CFG["default_place"]]
    return {"name": name, "full": lugar, "city": None, "lat": d[0], "lng": d[1], "abroad": False, "approx": True}


def fetch_agenda():
    a = CFG.get("agenda") or {}
    if not a.get("enabled"):
        return None
    today = NOW.astimezone(LIMA_TZ).date()
    start = today - dt.timedelta(days=a.get("days", 7))
    body = urllib.parse.urlencode({"opcion": "7", "id_funcionario": a["id_funcionario"],
                                   "fecha": f"{start:%d/%m/%Y} - {today:%d/%m/%Y}"}).encode()
    try:
        req = urllib.request.Request(a["url"], data=body, headers={
            "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded", "Referer": a["page"]})
        with urllib.request.urlopen(req, timeout=30) as r:
            rows = json.loads(r.read().decode()).get("data") or []
    except Exception as ex:  # noqa: BLE001
        print(f"Agenda oficial no disponible: {ex}")
        return None
    out, seen = [], set()
    for r in rows:
        hi, hf = r.get("hora_inicio"), r.get("hora_fin")
        if not r.get("fecha") or not hi or hi == "null":
            continue
        lugar = (r.get("lugar") or "").strip().rstrip(".").strip()
        key = (r["fecha"], hi, norm(lugar))
        if key in seen:
            continue
        seen.add(key)
        try:
            t0 = dt.datetime.strptime(f"{r['fecha']} {hi}", "%d/%m/%Y %H:%M").replace(tzinfo=LIMA_TZ)
            t1 = dt.datetime.strptime(f"{r['fecha']} {hf}", "%d/%m/%Y %H:%M").replace(tzinfo=LIMA_TZ) if hf and hf != "null" else t0
        except ValueError:
            continue
        out.append({"start": t0.isoformat(), "end": t1.isoformat(),
                    "minutes": max(0, int((t1 - t0).total_seconds() // 60)),
                    "subject": (r.get("asunto") or "").strip(), "lugar": lugar,
                    "state": classify(norm(r.get("asunto") or "")),
                    "place": agenda_place(lugar)})
    out.sort(key=lambda x: x["start"], reverse=True)
    print(f"{len(out):>3} actividades · Agenda oficial del Despacho Presidencial")
    return out


def next_place(title):
    """Próximo destino: solo si el propio titular habla de ella y usa un verbo en futuro."""
    t = norm(title)
    if not any(has_word(t, w) for w in CFG.get("subject_words", [])):
        return None
    return find_place(t, CFG["next_verbs"])


def collect():
    items, report = [], []
    for f in CFG["feeds"]:
        try:
            d = feedparser.parse(f["url"], agent=UA)
        except Exception as ex:  # noqa: BLE001
            report.append(f"ERROR {f['url']}: {ex}")
            continue
        report.append(f"{len(d.entries):>3} entradas · {f.get('name') or 'Google Noticias'} · {f['url'][:70]}")
        for e in d.entries:
            title = clean(e.get("title"))
            src = clean((e.get("source") or {}).get("title", ""))
            medio = f.get("name") or src
            if src in CFG.get("exclude_sources", []):
                continue
            if f.get("google"):
                for suffix in {src, medio}:
                    if suffix and title.endswith(" - " + suffix):
                        title = title[: -len(suffix) - 3]
                summary = ""  # Google Noticias no trae extracto propio del medio
            else:
                summary = excerpt(clean(e.get("summary")), CFG["excerpt_words"])
            date = entry_date(e)
            if not title or not date or (NOW - date).days > CFG["max_age_days"]:
                continue
            t = norm(title + " " + summary)
            if not is_about(t):
                continue
            items.append({
                "id": hashlib.sha1(norm(title)[:90].encode()).hexdigest()[:12],
                "title": title, "excerpt": summary, "source": medio or "Medio",
                "url": e.get("link", ""), "date": date.isoformat(),
                "state": classify(t),
                "place": locate(title, summary),
                "next": next_place(title),
            })
    return items, report


def main():
    if OUT.exists():
        try:
            GEOCACHE.update(json.loads(OUT.read_text(encoding="utf-8")).get("geocache", {}))
        except ValueError:
            pass
    fresh, report = collect()
    agenda = fetch_agenda()
    if agenda is None and OUT.exists():  # si la agenda falla, se conserva la última conocida
        try:
            agenda = json.loads(OUT.read_text(encoding="utf-8")).get("agenda")
        except ValueError:
            agenda = None
    agenda = agenda or []
    print("\n".join(report))

    old = []
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("items", [])
        except ValueError:
            pass

    by_id = {}
    for it in old + fresh:  # lo nuevo pisa a lo viejo; se conserva el que tenga extracto
        prev = by_id.get(it["id"])
        if prev and prev.get("excerpt") and not it.get("excerpt"):
            it["excerpt"] = prev["excerpt"]
        by_id[it["id"]] = it
    cutoff = NOW - dt.timedelta(days=CFG["max_age_days"])
    items = [i for i in by_id.values() if dt.datetime.fromisoformat(i["date"]) >= cutoff]
    items.sort(key=lambda i: i["date"], reverse=True)
    items = items[: CFG["max_items"]]

    week = NOW - dt.timedelta(days=7)
    counts = {k: 0 for k in CFG["states"]}
    for i in items:
        if dt.datetime.fromisoformat(i["date"]) >= week:
            counts[i["state"]] += 1

    last_place = next((i for i in items if i.get("place")), None)
    upcoming = next((i for i in items if i.get("next")
                     and (NOW - dt.datetime.fromisoformat(i["date"])).days <= 5), None)

    data = {
        "generated_at": NOW.isoformat(),
        "states": CFG["states"],
        "default_place": {"name": CFG["default_place"], "lat": CFG["places"][CFG["default_place"]][0],
                          "lng": CFG["places"][CFG["default_place"]][1], "abroad": False},
        "silence_limit": CFG["silence_hours"],
        "last_place": {"place": last_place["place"], "source": last_place["source"], "date": last_place["date"]} if last_place else None,
        "next": {"place": upcoming["next"], "source": upcoming["source"], "date": upcoming["date"],
                 "title": upcoming["title"], "url": upcoming["url"]} if upcoming else None,
        "counts_7d": counts,
        "total_7d": sum(counts.values()),
        "silence": CFG.get("silence"),
        "agenda": agenda[:40],
        "agenda_minutes_7d": sum(a["minutes"] for a in agenda),
        "agenda_page": (CFG.get("agenda") or {}).get("page"),
        "items": items,
        "geocache": GEOCACHE,
    }
    # Si solo cambió la hora de generación, no reescribir (evita commits vacíos).
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            prev["generated_at"] = data["generated_at"]
            if prev == data:
                print("Sin cambios.")
                return
        except ValueError:
            pass
    new_text = json.dumps(data, ensure_ascii=False, indent=1)
    OUT.write_text(new_text, encoding="utf-8")
    print(f"{len(items)} titulares guardados · {len(fresh)} recogidos en esta pasada.")


if __name__ == "__main__":
    main()
