"""
Genera catalog.json a partir del dataset 'crawlfeeds/ikea-us-products-dataset' (Kaggle).

Decisiones clave:
- Imágenes (Opción A, hecha bien): el dataset NO trae columna de imagen y las URLs
  de IKEA NO son construibles a ciegas (llevan un id interno tipo `pe######`).
  Solución: por cada producto seleccionado se descarga su `product_url` y se extrae
  la imagen real desde el meta `og:image`. Si falla, se cae a un placeholder y el
  <img onerror> del frontend lo maneja.
- Categorías: se usa el DEPARTAMENTO (primer nivel de breadcrumbs) y se traduce a
  español limpio para IKEA México. Esto elimina categorías vacías, mojibake
  ("Home D�cor") y la fragmentación en ~30 categorías de 1 producto.
- Muestreo: round-robin por departamento para lograr variedad visual y no terminar
  con 50 gabinetes de cocina.
"""

import kagglehub
import json
import os
import re
import concurrent.futures
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE_DIR, "catalog.json")
TARGET_TOTAL = 50
MXN_RATE = 17.5
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
PLACEHOLDER = "https://placehold.co/600x450?text=IKEA"

# Departamento (breadcrumbs[1]) -> categoría limpia en español.
# El orden define la prioridad de muestreo (los primeros llenan el grid antes).
DEPT_ES = {
    "Furniture": "Muebles",
    "Storage & organization": "Almacenamiento",
    "Lighting": "Iluminación",
    "Home Décor": "Decoración",
    "Home D�cor": "Decoración",   # variante con mojibake del dataset
    "Beds & mattresses": "Camas y colchones",
    "Home Textiles": "Textiles",
    "Rugs": "Alfombras",
    "Bathroom": "Baño",
    "Outdoor": "Exterior",
    "Baby & kids": "Bebé y niños",
    "Cookware & tableware": "Cocina y mesa",
    "Kitchen & appliances": "Cocina",
    "Home electronics": "Electrónica",
    "Gardening & plants": "Jardín y plantas",
    "Pet accessories": "Mascotas",
    "Laundry & cleaning": "Lavado y limpieza",
    "Home improvement": "Mejoras del hogar",
    "Winter holidays": "Temporada",
}
PRIORITY = list(DEPT_ES.values())


# ── 1. Cargar dataset ───────────────────────────────────────────────────────
def load_raw():
    path = kagglehub.dataset_download("crawlfeeds/ikea-us-products-dataset")
    print("Dataset path:", path)
    json_files = [
        os.path.join(r, f)
        for r, _, fs in os.walk(path)
        for f in fs
        if f.endswith(".json")
    ]
    if not json_files:
        raise FileNotFoundError(f"No JSON found in {path}")
    with open(json_files[0], encoding="utf-8") as f:
        return json.load(f)


# ── 2. Helpers de limpieza ──────────────────────────────────────────────────
def department(breadcrumbs):
    parts = str(breadcrumbs).split("/")
    return parts[1].strip() if len(parts) > 1 else ""


def strip_html(text):
    t = re.sub(r"<[^>]+>", " ", str(text))
    t = re.sub(r"\s+", " ", t).strip()
    # El dataset usa ' / ' para separar párrafos; nos quedamos con el primero útil.
    first = t.split(" / ")[0].strip()
    return (first or t)[:150]


def parse_price(val):
    try:
        return float(str(val).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def infer_style(text):
    t = text.lower()
    if any(w in t for w in ["white", "birch", "natural", "beige", "light", "oak"]):
        return ["escandinavo", "minimalista"]
    if any(w in t for w in ["black", "dark", "metal", "steel", "anthracite"]):
        return ["industrial", "moderno"]
    return ["escandinavo"]


def infer_colors(text):
    t = text.lower()
    colors = []
    if any(w in t for w in ["white", "blanco"]):            colors.append("blanco")
    if any(w in t for w in ["black", "negro", "anthracite"]): colors.append("negro")
    if any(w in t for w in ["birch", "oak", "natural", "pine"]): colors.append("madera")
    if any(w in t for w in ["grey", "gray", "gris"]):       colors.append("gris")
    if any(w in t for w in ["beige", "sand"]):              colors.append("beige")
    if any(w in t for w in ["blue", "azul"]):               colors.append("azul")
    if any(w in t for w in ["green", "verde"]):             colors.append("verde")
    return colors or ["neutro"]


# ── 3. Imagen real desde la página del producto (og:image) ──────────────────
_OG_RE = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I)
_IMG_RE = re.compile(r'https://www\.ikea\.com/[a-z]{2}/[a-z]{2}/images/products/[^"\' ]+_s5\.(?:jpg|jpeg)', re.I)


def fetch_image(product_url):
    """Devuelve la URL de la imagen real del producto, o '' si no es confiable."""
    if not product_url or product_url in ("nan", "None"):
        return ""
    try:
        html = urlopen(Request(product_url, headers=UA), timeout=20).read().decode("utf-8", "ignore")
    except (URLError, HTTPError, TimeoutError, OSError):
        return ""

    # og:image solo es válido si es una foto de producto (no un banner de categoría).
    m = _OG_RE.search(html)
    if m and "/images/products/" in m.group(1):
        return m.group(1)

    # Respaldo: imagen del catálogo cuyo slug coincide con el del producto (evita
    # tomar fotos de productos "relacionados" que aparecen en la misma página).
    slug = product_url.rstrip("/").split("/")[-1]
    stem = re.sub(r"-?[a-z]?\d{6,}$", "", slug).split("-")[0]
    if stem:
        for u in _IMG_RE.findall(html):
            if u.split("/")[-1].startswith(stem):
                return u
    return ""


# ── 4. Selección round-robin por departamento ───────────────────────────────
def bucketize(raw):
    """Agrupa productos válidos por categoría (en español), sin duplicados."""
    seen = set()
    by_dept = {}
    for r in raw:
        if r.get("availability") != "InStock":
            continue
        sku = str(r.get("sku") or "").strip()
        if not sku or sku in seen:
            continue
        cat_es = DEPT_ES.get(department(r.get("breadcrumbs", "")))
        if not cat_es:
            continue  # departamento vacío / desconocido -> se descarta
        if parse_price(r.get("product_price")) <= 0:
            continue
        if not str(r.get("product_url") or "").startswith("http"):
            continue
        seen.add(sku)
        by_dept.setdefault(cat_es, []).append(r)
    return by_dept


def round_robin(by_dept, per_dept):
    """Lista ordenada (cat, row) tomando hasta `per_dept` de cada categoría por turnos."""
    ordered = []
    for idx in range(per_dept):
        for cat in PRIORITY:
            bucket = by_dept.get(cat, [])
            if idx < len(bucket):
                ordered.append((cat, bucket[idx]))
    return ordered


# ── 5. Construcción del catálogo ────────────────────────────────────────────
def build():
    raw = load_raw()
    print(f"Registros crudos: {len(raw)}")
    by_dept = bucketize(raw)

    # Sobre-seleccionamos candidatos (varios por categoría) para luego quedarnos
    # con los que SÍ tienen imagen real, manteniendo variedad por categoría.
    candidates = round_robin(by_dept, per_dept=6)
    print(f"Candidatos a evaluar: {len(candidates)}. Descargando imágenes reales de IKEA…")

    urls = [str(r.get("product_url") or "") for _, r in candidates]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        images = list(ex.map(fetch_image, urls))

    # Reagrupamos por categoría, priorizando los que tienen imagen real.
    with_img, without_img = {}, {}
    for (cat, r), img in zip(candidates, images):
        (with_img if img else without_img).setdefault(cat, []).append((r, img))

    # Round-robin final: primero productos con imagen, luego rellenamos con el resto.
    selected = []
    for pool in (with_img, without_img):
        idx = 0
        while len(selected) < TARGET_TOTAL:
            progressed = False
            for cat in PRIORITY:
                bucket = pool.get(cat, [])
                if idx < len(bucket):
                    r, img = bucket[idx]
                    selected.append((cat, r, img))
                    progressed = True
                    if len(selected) >= TARGET_TOTAL:
                        break
            if not progressed:
                break
            idx += 1
        if len(selected) >= TARGET_TOTAL:
            break

    cats_used = sorted(set(c for c, _, _ in selected))
    print(f"Productos seleccionados: {len(selected)} en {len(cats_used)} categorías")

    catalog = []
    real_imgs = 0
    for cat_es, r, img in selected:
        name = str(r.get("product_title") or "Producto IKEA").strip()
        if img:
            real_imgs += 1
        text_for_inference = name + " " + str(r.get("raw_product_details", ""))
        catalog.append({
            "id":          str(r.get("sku") or len(catalog)),
            "nombre":      name,
            "categoria":   cat_es,
            "precio_mxn":  round(parse_price(r.get("product_price")) * MXN_RATE),
            "descripcion": strip_html(r.get("raw_product_details") or name),
            "estilo":      infer_style(text_for_inference),
            "colores":     infer_colors(text_for_inference),
            "image_url":   img or PLACEHOLDER,
            "product_url": str(r.get("product_url") or ""),
        })

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    print(f"catalog.json guardado: {len(catalog)} productos "
          f"({real_imgs} con imagen real de IKEA, {len(catalog) - real_imgs} con placeholder)")


if __name__ == "__main__":
    build()
