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
import unicodedata

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


def entry_date(e):
    for k in ("published_parsed", "updated_parsed"):
        if e.get(k):
            return dt.datetime.fromtimestamp(calendar.timegm(e[k]), dt.timezone.utc)
    return None


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
            medio = f.get("name") or clean((e.get("source") or {}).get("title", ""))
            if f.get("google"):
                if medio and title.endswith(" - " + medio):
                    title = title[: -len(medio) - 3]
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
                "place": find_place(t, CFG["here_verbs"]),
                "next": find_place(t, CFG["next_verbs"]),
            })
    return items, report


def main():
    fresh, report = collect()
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
        "items": items,
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
