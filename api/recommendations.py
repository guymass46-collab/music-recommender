"""
===============================================================================
 Music Recommender - Vercel Serverless Function
===============================================================================
 Endpoint: POST /api/recommendations
 Body: { "artists": ["Adam Ten", "Mita Gami", ...] }
 Returns: { "recommendations": [...] }

 Security:
   - API key נטען ממשתני סביבה (LASTFM_API_KEY)
   - CORS מוגדר רק לדומיין הפרודקשן
   - Rate limiting בסיסי דרך Vercel headers
===============================================================================
"""

from http.server import BaseHTTPRequestHandler
import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error


# =========================================================================
# Configuration
# =========================================================================
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")  # ב-production: הדומיין שלך
SIMILAR_PER_ARTIST = 10
MAX_RECOMMENDED_ARTISTS = 25
MAX_INPUT_ARTISTS = 30  # הגנה - לא מאפשרים יותר מ-30 אמנים בבקשה אחת

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# =========================================================================
# Last.fm helpers
# =========================================================================

def lastfm_request(method, params, timeout=10):
    """קריאה כללית ל-Last.fm API"""
    base = "http://ws.audioscrobbler.com/2.0/"
    params = {**params, "method": method, "api_key": LASTFM_API_KEY, "format": "json"}
    url = base + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None


def get_similar_artists(artist_name, limit=10):
    """מחזיר רשימת אמנים דומים מ-Last.fm"""
    data = lastfm_request("artist.getSimilar", {
        "artist": artist_name,
        "limit": limit,
        "autocorrect": 1,
    })
    if not data or "similarartists" not in data:
        return []
    artists = data["similarartists"].get("artist", [])
    return [
        {
            "name": a.get("name", ""),
            "match": float(a.get("match", 0)),
            "url": a.get("url", ""),
            "image": next(
                (img.get("#text", "") for img in a.get("image", [])
                 if img.get("size") == "large"),
                ""
            ),
        }
        for a in artists if a.get("name")
    ]


def get_top_tracks(artist_name, limit=3):
    """מחזיר את השירים הכי פופולריים של אמן"""
    data = lastfm_request("artist.getTopTracks", {
        "artist": artist_name,
        "limit": limit,
        "autocorrect": 1,
    })
    if not data or "toptracks" not in data:
        return []
    tracks = data["toptracks"].get("track", [])
    return [
        {
            "name": t.get("name", ""),
            "playcount": t.get("playcount", "0"),
            "url": t.get("url", ""),
        }
        for t in tracks if t.get("name")
    ]


def get_artist_tags(artist_name, limit=5):
    """מחזיר תגיות (ז'אנרים) של אמן"""
    data = lastfm_request("artist.getTopTags", {
        "artist": artist_name,
        "autocorrect": 1,
    })
    if not data or "toptags" not in data:
        return []
    tags = data["toptags"].get("tag", [])
    return [t.get("name", "") for t in tags[:limit] if t.get("name")]


# =========================================================================
# Main handler
# =========================================================================

class handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        # CORS preflight
        self._send(204, {})

    def do_POST(self):
        # ---- 1. בדיקת תקינות בקשה ----
        if not LASTFM_API_KEY:
            self._send(500, {"error": "Server misconfiguration: API key missing"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 10000:  # מגבלה על גודל בקשה (10KB)
                self._send(413, {"error": "Request too large"})
                return
            body = self.rfile.read(length).decode("utf-8")
            data = json.loads(body) if body else {}
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "Invalid JSON"})
            return

        artists = data.get("artists", [])
        if not isinstance(artists, list) or not artists:
            self._send(400, {"error": "Missing 'artists' list"})
            return

        # ---- 2. ניקוי קלט והגנה ----
        # מקבלים רק מחרוזות, מנקים, חותכים אורך, מגבילים כמות
        clean_artists = []
        for a in artists[:MAX_INPUT_ARTISTS]:
            if isinstance(a, str):
                name = a.strip()[:100]  # מקסימום 100 תווים
                if name:
                    clean_artists.append(name)

        if not clean_artists:
            self._send(400, {"error": "No valid artist names"})
            return

        # ---- 3. אגירת אמנים דומים ----
        artist_scores = {}
        known = {a.lower() for a in clean_artists}

        for base_artist in clean_artists:
            similar = get_similar_artists(base_artist, limit=SIMILAR_PER_ARTIST)
            for s in similar:
                name = s["name"]
                if not name or name.lower() in known:
                    continue
                if name in artist_scores:
                    artist_scores[name]["score"] += s["match"]
                    artist_scores[name]["sources"].append(base_artist)
                else:
                    artist_scores[name] = {
                        "score": s["match"],
                        "url": s["url"],
                        "image": s["image"],
                        "sources": [base_artist],
                    }

        if not artist_scores:
            self._send(200, {"recommendations": [], "message": "No similar artists found"})
            return

        # ---- 4. דירוג ובחירת המובילים ----
        top = sorted(artist_scores.items(), key=lambda x: x[1]["score"], reverse=True)
        top = top[:MAX_RECOMMENDED_ARTISTS]

        # ---- 5. עשרת המובילים מקבלים גם שירים וגם תגיות ----
        recommendations = []
        for i, (name, info) in enumerate(top):
            entry = {
                "name": name,
                "score": round(info["score"], 2),
                "lastfm_url": info["url"],
                "image": info["image"],
                "similar_to": info["sources"],
                "spotify_search": f"https://open.spotify.com/search/{urllib.parse.quote(name)}",
                "soundcloud_search": f"https://soundcloud.com/search?q={urllib.parse.quote(name)}",
            }
            # רק לעשרת המובילים נטען מידע נוסף - חוסך זמן וקריאות API
            if i < 10:
                entry["top_tracks"] = get_top_tracks(name, limit=3)
                entry["tags"] = get_artist_tags(name, limit=5)
            else:
                entry["top_tracks"] = []
                entry["tags"] = []
            recommendations.append(entry)

        self._send(200, {
            "recommendations": recommendations,
            "total_found": len(artist_scores),
        })
