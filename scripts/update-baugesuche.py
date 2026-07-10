#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update-baugesuche.py  —  Tages-Updater für den Nachbar-Radar (Kanton Zürich)
============================================================================

Lädt die OFFIZIELLEN, täglich aktualisierten Baugesuch-Daten des Kantons Zürich
(Open Government Data), rechnet die Koordinaten von LV95 (EPSG:2056) nach WGS84
(EPSG:4326) um, verschlankt die Felder und schreibt eine schlanke Karten-Datei,
die der Nachbar-Radar liest.

    python update-baugesuche.py                    ->  schreibt baugesuche.geojson
    python update-baugesuche.py docs/baugesuche.geojson   ->  frei wählbarer Zielpfad

Quelle (© Kanton Zürich, Amt für Statistik und Daten, OGD):
  Datensatz : "Baugesuche im Kanton Zürich" (2982@statistisches-amt-kanton-zuerich)
  Metadaten : https://daten.statistik.zh.ch/ogd/zhweb.json
  GeoPackage: https://daten.statistik.zh.ch/ogd/daten/ressourcen/KTZH_00002982_00006403.gpkg

Voraussetzung:  pip install geopandas pyogrio

Ehrliche Grenzen: nur Kanton Zürich; KEINE Namen/Adressen der Bauherrschaft
(Datenschutz); ein Baugesuch belegt kein realisiertes Bauvorhaben.
"""

import json, sys, os, datetime, tempfile, urllib.request

META_URL = "https://daten.statistik.zh.ch/ogd/zhweb.json"
GPKG_FALLBACK = "https://daten.statistik.zh.ch/ogd/daten/ressourcen/KTZH_00002982_00006403.gpkg"
DATASET_ID = "2982"
DEFAULT_OUT = "baugesuche.geojson"

FIELD_CANDIDATES = {
    "description": ["beschreibung", "bauprojekt", "projekt", "bauvorhaben", "vorhaben",
                    "projektbeschrieb", "art_des_gesuchs", "gegenstand", "description"],
    "status":      ["status", "verfahrensstand", "stand", "gesuchsstatus", "publikationsart"],
    "gemeinde":    ["gemeinde", "gemeindename", "municipality", "ort", "politische_gemeinde"],
    "bfs":         ["bfs", "bfs_nr", "bfsnr", "bfs_gemeindenummer", "gemeinde_bfs"],
    "kataster":    ["kataster", "katasternummer", "kat_nr", "parzelle", "parzellennummer",
                    "grundstueck", "grundstuecksnummer", "egrid"],
    "publication": ["publikation", "publikationsdatum", "datum_publikation", "datum",
                    "auflage_von", "auflage_start", "ausschreibungsdatum", "date"],
}


def log(m): print(m, flush=True)


def resolve_gpkg_url():
    """Aktuelle GeoPackage-URL aus den Metadaten (überlebt URL-Wechsel); sonst Fallback."""
    try:
        with urllib.request.urlopen(META_URL, timeout=90) as r:
            meta = json.load(r)
        datasets = meta.get("dataset") if isinstance(meta, dict) else meta
        for ds in (datasets or []):
            if DATASET_ID in str(ds.get("identifier", "")):
                for dist in ds.get("distribution", []):
                    url = dist.get("downloadUrl") or dist.get("accessUrl") or ""
                    if url.lower().endswith(".gpkg"):
                        log(f"  GeoPackage-URL aus Metadaten: {url}")
                        return url
    except Exception as e:
        log(f"  Metadaten nicht auflösbar ({e}) – nutze Fallback-URL.")
    return GPKG_FALLBACK


def download(url):
    """GeoPackage lokal ablegen (robuster als /vsicurl)."""
    fd, path = tempfile.mkstemp(suffix=".gpkg")
    os.close(fd)
    log(f"  Lade herunter: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "nachbar-radar/1.0"})
    with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    log(f"  {os.path.getsize(path)/1e6:.1f} MB gespeichert.")
    return path


def pick(cols_lower, key):
    for cand in FIELD_CANDIDATES[key]:
        if cand in cols_lower:
            return cols_lower[cand]
    return None


def clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return None if s in ("", "nan", "None", "NaT") else s


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("OUT_FILE", DEFAULT_OUT)
    try:
        import geopandas as gpd
    except ImportError:
        log("FEHLER: geopandas fehlt.  ->  pip install geopandas pyogrio")
        sys.exit(1)

    url = resolve_gpkg_url()
    gpkg = download(url)
    try:
        gdf = gpd.read_file(gpkg)
    finally:
        try:
            os.remove(gpkg)
        except OSError:
            pass
    log(f"  {len(gdf)} Datensätze, CRS={gdf.crs}")

    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    cols = {c.lower(): c for c in gdf.columns}
    mapping = {k: pick(cols, k) for k in FIELD_CANDIDATES}
    log("  Erkannte Felder: " + ", ".join(f"{k}->{v}" for k, v in mapping.items()))

    features = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        pt = geom if geom.geom_type == "Point" else geom.centroid
        props = {k: (None if col is None else clean(row.get(col))) for k, col in mapping.items()}
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(float(pt.x), 6), round(float(pt.y), 6)]},
            "properties": props,
        })

    out = {
        "type": "FeatureCollection",
        "updated": datetime.date.today().isoformat(),
        "source": "© Kanton Zürich (OGD) – Baugesuche im Kanton Zürich",
        "count": len(features),
        "features": features,
    }
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    log(f"OK: {len(features)} Baugesuche -> {out_path} (Stand {out['updated']})")


if __name__ == "__main__":
    main()
