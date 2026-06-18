"""
NickelTrack Flask app (v2 — scanner + local lookup + Open Food Facts fallback).

Routes:
    GET  /                          index page (search + meal builder UI)
    GET  /api/search?q=...          JSON food search by name
    GET  /api/food/<id>             JSON food detail
    GET  /api/lookup?barcode=...    JSON: lookup by barcode (local DB → OFF fallback)
    GET  /api/config                JSON app config (daily targets)
    POST /api/totals                JSON {items: [{food_id, servings}, ...]} -> daily totals

Single-tenant for v2. State held client-side (localStorage) — no DB writes for the day-log.
The lookup route DOES write to foods (caching OFF results).
"""
from __future__ import annotations

import os
import re
import time
import urllib.parse
import urllib.request
import json
from typing import Optional

from env_loader import get_pgvector_dsn

from flask import Flask, jsonify, render_template, request
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__, template_folder="templates", static_folder="static")

DB_DSN = get_pgvector_dsn()


def db():
    """Per-request connection."""
    return psycopg2.connect(DB_DSN, cursor_factory=RealDictCursor)


# ─────────────────────────────────────────────────────────────
# Lookup helpers (v2 — barcode → food)
# ─────────────────────────────────────────────────────────────

# Cache for OFF responses in-memory, scoped to this process. Real production
# would use Redis or a per-barcode table, but for v2 a 5-min TTL is enough to
# deduplicate bursts of the same scan without thrashing OFF.
_OFF_CACHE: dict[str, tuple[float, dict]] = {}
_OFF_CACHE_TTL = 300  # seconds

# OFF category tags → our 3-tier category. Conservative default: "high" because
# underestimating nickel exposure is worse than overestimating it.
#
# Source: EFSA 2020 Scientific Opinion on Nickel in Food — table of average
# nickel concentrations by food category. Tags are lowercased to match
# OFF's `categories_tags` format like "en:chocolates".
_OFF_CATEGORY_MAP: dict[str, str] = {
    # HIGH (>500 µg/kg)
    "chocolates": "high",
    "cocoa": "high",
    "cocoa-powders": "high",
    "cocoa-pastes": "high",
    "cocoa-butters": "high",
    "nuts": "high",
    "tree-nuts": "high",
    "cashew-nuts": "high",
    "hazelnuts": "high",
    "walnuts": "high",
    "pecan-nuts": "high",
    "brazil-nuts": "high",
    "almonds": "high",
    "pistachios": "high",
    "macadamia-nuts": "high",
    "legumes": "high",
    "lentils": "high",
    "chickpeas": "high",
    "beans": "high",
    "kidney-beans": "high",
    "white-beans": "high",
    "black-beans": "high",
    "pinto-beans": "high",
    "soybeans": "high",
    "soya-beans": "high",
    "soya": "high",
    "tofu": "high",
    "tempeh": "high",
    "peanuts": "high",
    "peanut-butters": "high",
    "oats": "high",
    "rolled-oats": "high",
    "oat-flakes": "high",
    "oat-milks": "high",
    "buckwheat": "high",
    "buckwheats": "high",
    "wheat-germs": "high",
    "wheat-brans": "high",
    "whole-grains": "high",
    "mueslis": "high",
    "granolas": "high",
    "spinaches": "high",
    "kales": "high",
    "broccoli": "medium",  # leafy but lower than spinach
    "rhubarbs": "high",
    "asparaguses": "high",
    "green-beans": "high",
    "green-teas": "high",
    "black-teas": "high",
    "teas": "high",
    "herbal-teas": "high",
    "canned-sardines": "high",
    "shellfishes": "high",
    "oysters": "high",
    "clams": "high",
    "mussels": "high",
    # MEDIUM (100–500)
    "tomatoes": "medium",
    "tomato-purees": "medium",
    "tomato-pastes": "medium",
    "carrots": "medium",
    "carrot": "medium",
    "onions": "medium",
    "garlic": "medium",
    "leeks": "medium",
    "cabbage": "medium",
    "cabbages": "medium",
    "lettuces": "medium",
    "apples": "medium",
    "pears": "medium",
    "pear": "medium",
    "oranges": "medium",
    "orange": "medium",
    "citrus-fruits": "medium",
    "pineapples": "medium",
    "pineapple": "medium",
    "figs": "medium",
    "fig": "medium",
    "raspberries": "medium",
    "raspberry": "medium",
    "pine-nuts": "medium",
    "sunflower-seeds": "medium",
    "sesame-seeds": "medium",
    "pumpkin-seeds": "medium",
    "rice": "medium",
    "brown-rices": "medium",
    "wholemeal-breads": "medium",
    "whole-wheat-breads": "medium",
    "rye-breads": "medium",
    "pastas": "medium",
    "whole-wheat-pastas": "medium",
    "marmites": "medium",
    "bouillons": "medium",
    "salmon": "medium",
    "tuna": "medium",
    "tunas": "medium",
    "canned-tunas": "medium",
    "dark-chocolates": "high",  # already in high
    "milks": "medium",
    "whole-milks": "medium",
    "semi-skimmed-milks": "medium",
    # LOW (<100)
    "rice-milks": "low",
    "almond-milks": "low",
    "coconut-milks": "low",
    "oat-milks": "low",
    "white-breads": "low",
    "white-rices": "low",
    "white-pastas": "low",
    "yogurts": "low",
    "cheeses": "low",
    "cottage-cheeses": "low",
    "cream-cheeses": "low",
    "mozzarella": "low",
    "cheddar": "low",
    "eggs": "low",
    "chickens": "low",
    "chicken-breasts": "low",
    "turkeys": "low",
    "beefs": "low",
    "pork-meats": "low",
    "lamb-meats": "low",
    "fish": "low",
    "white-fishes": "low",
    "cods": "low",
    "haddocks": "low",
    "prawns": "low",
    "shrimps": "low",
    "cucumbers": "low",
    "zucchinis": "low",
    "bell-peppers": "low",
    "mushrooms": "low",
    "potatoes": "low",
    "sweet-potatoes": "low",
    "corns": "low",
    "sweet-corns": "low",
    "peas": "low",
    "green-peas": "low",
    "watermelons": "low",
    "bananas": "low",
    "blueberries": "low",
    "strawberries": "low",
    "grapes": "low",
    "kiwis": "low",
    "mangoes": "low",
    "papayas": "low",
    "avocados": "low",
    "lemons": "low",
    "limes": "low",
}


def _estimate_category_from_tags(categories_tags: list[str]) -> tuple[str, str]:
    """Map OFF categories_tags to our 3-tier category.

    Returns (category, matched_tag). category is one of: "high", "medium", "low", "unknown".
    matched_tag is the OFF tag that triggered the match (for the UI to show provenance),
    or "" if nothing matched.
    """
    for tag in categories_tags or []:
        # OFF tags are "en:foo" or "en:foo-bars" — strip prefix and lowercase
        slug = tag.split(":", 1)[-1].lower()
        if slug in _OFF_CATEGORY_MAP:
            return _OFF_CATEGORY_MAP[slug], slug
    return "unknown", ""


def _normalize_barcode(raw: str) -> str:
    """Extract digits from a scanned string. Handles OFF URLs too.

    Examples:
        "0123456789012"          -> "0123456789012"
        "https://world.openfoodfacts.org/product/3017620422003" -> "3017620422003"
        " 0 12345 6789012 "       -> "0123456789012"
    """
    if not raw:
        return ""
    # If it's a URL, try to extract the product ID from the path
    if raw.startswith("http://") or raw.startswith("https://"):
        m = re.search(r"/product/(\d+)", raw)
        if m:
            return m.group(1)
    # Otherwise: strip non-digits
    digits = re.sub(r"\D", "", raw)
    # Reasonable bounds for a barcode (EAN-8 = 8, EAN-13 = 13, UPC-A = 12, etc.)
    if 6 <= len(digits) <= 14:
        return digits
    return ""


def _fetch_off(barcode: str) -> Optional[dict]:
    """Fetch product from Open Food Facts. Returns the 'product' dict or None.

    Uses the v2 API: https://openfoodfacts.github.io/openfoodfacts-server/api/
    Caches the result in-process for 5 minutes.
    """
    now = time.time()
    if barcode in _OFF_CACHE:
        ts, data = _OFF_CACHE[barcode]
        if now - ts < _OFF_CACHE_TTL:
            return data
        del _OFF_CACHE[barcode]

    url = f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json?fields=code,product_name,product_name_en,brands,brands_tags,image_url,image_front_url,ingredients_text,ingredients_text_en,categories_tags,categories,nutriments,quantity,allergens,additives_tags"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "NickelTrack/2.0 (low-nickel-diet-tracker; +https://github.com/ChoznJuan/nickeltrack)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
    except Exception as e:
        # Network error or OFF rate-limit. Log and move on.
        app.logger.warning(f"OFF lookup failed for {barcode}: {e}")
        return None
    if data.get("status") != 1 or not data.get("product"):
        _OFF_CACHE[barcode] = (now, None)  # cache the negative result too
        return None
    product = data["product"]
    _OFF_CACHE[barcode] = (now, product)
    return product


def _serialize_food_row(row: dict) -> dict:
    """Coerce Decimal → float and add the `avoid` flag for the UI."""
    for k in ("nickel_ug_per_serving", "nickel_ug_per_kg", "serving_grams"):
        if row.get(k) is not None:
            row[k] = float(row[k])
    row["avoid"] = (row.get("points") is None)
    return row


# ─────────────────────────────────────────────────────────────
# HTML
# ─────────────────────────────────────────────────────────────
@app.get("/")
def index():
    return render_template("index.html")


# ─────────────────────────────────────────────────────────────
# PWA: manifest + service worker
# Both files live in /static/ and Flask normally serves them via
# the static route, but we need explicit Content-Type for the
# manifest and explicit no-cache for the service worker (otherwise
# browsers will keep using a stale sw.js after a deploy).
# ─────────────────────────────────────────────────────────────
from flask import send_from_directory  # noqa: E402


@app.get("/manifest.webmanifest")
def pwa_manifest():
    return send_from_directory(
        app.static_folder,
        "manifest.webmanifest",
        mimetype="application/manifest+json",
    )


@app.get("/sw.js")
def pwa_service_worker():
    resp = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    # Service workers must NOT be cached by the browser — otherwise
    # the new version won't take effect after a deploy.
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


# ─────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────
@app.get("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    limit = min(int(request.args.get("limit", 25)), 100)
    category = request.args.get("category")  # optional: high|medium|low

    where = []
    params = []
    if q:
        where.append("LOWER(name) LIKE %s")
        params.append(f"%{q.lower()}%")
    if category in ("high", "medium", "low"):
        where.append("category = %s")
        params.append(category)

    sql = """
        SELECT f.id, f.name, f.category, f.nickel_ug_per_serving,
               f.points, f.source,
               s.description AS serving, s.grams AS serving_grams
        FROM nickeltrack.foods f
        LEFT JOIN nickeltrack.servings s ON s.id = f.serving_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY (points IS NULL) DESC, points DESC NULLS LAST, f.name ASC LIMIT %s"
    params.append(limit)

    with db() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    # Convert Decimals to floats for JSON
    for r in rows:
        if r["nickel_ug_per_serving"] is not None:
            r["nickel_ug_per_serving"] = float(r["nickel_ug_per_serving"])
        if r["serving_grams"] is not None:
            r["serving_grams"] = float(r["serving_grams"])
        # Avoid flag for client convenience
        r["avoid"] = (r["points"] is None)
    return jsonify({"results": rows, "count": len(rows), "query": q})


@app.get("/api/food/<int:food_id>")
def api_food(food_id):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT f.*, s.description AS serving, s.grams AS serving_grams
            FROM nickeltrack.foods f
            LEFT JOIN nickeltrack.servings s ON s.id = f.serving_id
            WHERE f.id = %s
            """,
            (food_id,),
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "not_found"}), 404
    for k in ("nickel_ug_per_serving", "nickel_ug_per_kg", "serving_grams"):
        if row.get(k) is not None:
            row[k] = float(row[k])
    row["avoid"] = (row["points"] is None)
    return jsonify(row)


# ─────────────────────────────────────────────────────────────
# /api/lookup?barcode=...  (v2 — scanner endpoint)
#
# Flow:
#   1. Sanitize the input (digits only, accept OFF URLs)
#   2. Try local DB by off_barcode
#   3. If miss, ask Open Food Facts; cache result in `foods` if hit
#   4. Return one of: {source: "local"} | {source: "off"} | {source: "not_found"}
# ─────────────────────────────────────────────────────────────
@app.get("/api/lookup")
def api_lookup():
    raw = (request.args.get("barcode") or "").strip()
    if not raw:
        return jsonify({"error": "barcode required"}), 400
    barcode = _normalize_barcode(raw)
    if not barcode:
        return jsonify({"error": "could not extract a valid barcode from input", "raw": raw}), 400

    # 1) Local DB
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT f.id, f.name, f.category, f.nickel_ug_per_serving,
                   f.points, f.source, f.source_ref, f.off_barcode, f.notes,
                   s.description AS serving, s.grams AS serving_grams
            FROM nickeltrack.foods f
            LEFT JOIN nickeltrack.servings s ON s.id = f.serving_id
            WHERE f.off_barcode = %s
            """,
            (barcode,),
        )
        row = cur.fetchone()
    if row:
        food = _serialize_food_row(dict(row))
        food["source_origin"] = "local"
        return jsonify(food)

    # 2) Open Food Facts fallback
    product = _fetch_off(barcode)
    if not product:
        return jsonify(
            {
                "source_origin": "not_found",
                "barcode": barcode,
                "message": "Not in our database or Open Food Facts.",
            }
        ), 404

    # 3) Cache the OFF result in our DB (without nickel score)
    name = (product.get("product_name_en") or product.get("product_name") or "").strip()
    if not name:
        return jsonify(
            {
                "source_origin": "not_found",
                "barcode": barcode,
                "message": "OFF returned a product with no name.",
            }
        ), 404
    brands = ", ".join(product.get("brands_tags", []) or [product.get("brands", "")]).strip(", ")
    categories_tags = product.get("categories_tags", []) or []
    category, matched_tag = _estimate_category_from_tags(categories_tags)
    notes_parts = []
    if brands:
        notes_parts.append(f"Brand: {brands}")
    if matched_tag:
        notes_parts.append(f"Category match: {matched_tag}")
    notes_parts.append("Imported from Open Food Facts. Nickel estimate based on food category only — verify before relying on it.")
    notes = " · ".join(notes_parts)
    image_url = product.get("image_front_url") or product.get("image_url")
    ingredients = product.get("ingredients_text_en") or product.get("ingredients_text") or ""
    serving_desc = product.get("quantity") or None
    # For OFF foods we don't have a measured nickel value. category-based estimate
    # gives us a category tier but no points. The UI will show "⚠️ estimate" so the
    # user knows not to trust it as a precise value.
    nickel_ug = None
    points = None
    # Avoid flag: if category is "high", mark as avoid (points = NULL means AVOID)
    if category == "high":
        points = None
    elif category in ("medium", "low"):
        # Rough estimate from our local averages:
        #   medium avg = 35 µg/serving → ~4 pts
        #   low avg = 4.6 µg/serving → ~0 pts
        # Mark as estimate by setting a low confidence via notes (no DB column for it)
        if category == "medium":
            nickel_ug = 35.0
            points = 4
        else:
            nickel_ug = 5.0
            points = 0

    # Insert or update (idempotent on off_barcode)
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO nickeltrack.foods
                (name, category, nickel_ug_per_serving, points, source, source_ref,
                 off_barcode, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (off_barcode) DO UPDATE SET
                name = EXCLUDED.name,
                category = EXCLUDED.category,
                nickel_ug_per_serving = EXCLUDED.nickel_ug_per_serving,
                points = EXCLUDED.points,
                source = EXCLUDED.source,
                source_ref = EXCLUDED.source_ref,
                notes = EXCLUDED.notes,
                updated_at = now()
            RETURNING id, name, category, nickel_ug_per_serving, points, source, off_barcode
            """,
            (
                name[:500],
                category,
                nickel_ug,
                points,
                "openfoodfacts",
                f"https://world.openfoodfacts.org/product/{barcode}",
                barcode,
                notes[:2000],
            ),
        )
        inserted = cur.fetchone()
        conn.commit()

    return jsonify({
        "id": inserted["id"],
        "name": inserted["name"],
        "category": inserted["category"],
        "nickel_ug_per_serving": float(inserted["nickel_ug_per_serving"]) if inserted["nickel_ug_per_serving"] is not None else None,
        "points": inserted["points"],
        "source": inserted["source"],
        "off_barcode": inserted["off_barcode"],
        "serving": serving_desc,
        "avoid": (inserted["points"] is None),
        "estimated": True,
        "matched_tag": matched_tag,
        "image_url": image_url,
        "ingredients": ingredients,
        "source_origin": "off",
    })


@app.get("/api/config")
def api_config():
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT key, value FROM nickeltrack.config")
        cfg = {r["key"]: r["value"] for r in cur.fetchall()}
    # Coerce numeric config values
    for k in ("daily_target_ug", "daily_target_pts", "child_target_pts", "sensitive_target_pts"):
        if k in cfg:
            cfg[k] = float(cfg[k])
    return jsonify(cfg)


@app.post("/api/totals")
def api_totals():
    """Compute daily totals from a list of meal items.

    Body: {"items": [{"food_id": int, "servings": float}, ...],
           "profile": "adult"|"child"|"sensitive"  (default "adult")}
    """
    body = request.get_json(force=True) or {}
    items = body.get("items") or []
    profile = body.get("profile", "adult")

    if not isinstance(items, list) or not items:
        return jsonify({"ug": 0.0, "points": 0, "items": [], "target_ug": 150.0, "target_pts": 15.0})

    # Resolve profile target
    target_pts_key = {
        "adult": "daily_target_pts",
        "child": "child_target_pts",
        "sensitive": "sensitive_target_pts",
    }.get(profile, "daily_target_pts")

    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT key, value FROM nickeltrack.config")
        cfg = {r["key"]: float(r["value"]) for r in cur.fetchall()}

        total_ug = 0.0
        total_pts = 0.0
        avoid_ug = 0.0  # subset that came from "avoid" foods (>100 µg/serving)
        breakdown = []
        for it in items:
            try:
                fid = int(it["food_id"])
                servings = float(it.get("servings", 1.0))
            except (KeyError, ValueError, TypeError):
                continue
            cur.execute(
                "SELECT id, name, category, nickel_ug_per_serving, points FROM nickeltrack.foods WHERE id = %s",
                (fid,),
            )
            f = cur.fetchone()
            if not f:
                continue
            ug = float(f["nickel_ug_per_serving"] or 0) * servings
            avoid = (f["points"] is None) and servings > 0 and ug > 0
            # Per-item points: NULL for avoid foods so the UI shows AVOID, not 0.
            # Avoid foods still contribute their µg to the daily total so the user
            # gets warned when they blow past their target.
            if avoid:
                pts_value = None
                avoid_ug += ug
            else:
                pts_value = (float(f["points"]) if f["points"] is not None else 0.0) * servings
                total_pts += pts_value
            total_ug += ug
            breakdown.append({
                "food_id": fid,
                "name": f["name"],
                "category": f["category"],
                "servings": servings,
                "ug": round(ug, 2),
                "points": round(pts_value, 2) if pts_value is not None else None,
                "avoid": avoid,
            })

    return jsonify({
        "ug": round(total_ug, 2),
        "points": round(total_pts, 2),
        "avoid_ug": round(avoid_ug, 2),
        "items": breakdown,
        "target_ug": cfg.get("daily_target_ug", 150.0),
        "target_pts": cfg.get(target_pts_key, cfg.get("daily_target_pts", 15.0)),
        "profile": profile,
    })


# ─────────────────────────────────────────────────────────────
# Healthcheck (for deployment verification)
# ─────────────────────────────────────────────────────────────
@app.get("/healthz")
def healthz():
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()
            cur.execute("SELECT COUNT(*) AS n FROM nickeltrack.foods")
            n = cur.fetchone()["n"]
        return jsonify({"ok": True, "foods": n})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5100))
    app.run(host="0.0.0.0", port=port, debug=False)
