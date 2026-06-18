"""
NickelTrack Flask app (v1 — read-only reference).

Routes:
    GET  /                    index page (search + meal builder UI)
    GET  /api/search?q=...    JSON food search by name
    GET  /api/food/<id>       JSON food detail
    GET  /api/config          JSON app config (daily targets)
    POST /api/totals          JSON {items: [{food_id, servings}, ...]} -> daily totals

Single-tenant for v1. State held client-side (localStorage) — no DB writes.
"""
from __future__ import annotations

import os

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
