"""
===============================================================================
 Music Recommender - Vercel Serverless Function
===============================================================================
 Endpoints:
   GET  /             -> serves index.html
   POST /api/recommendations -> returns recommendations with artist images

 Sources:
   - Last.fm  -> similar artists, top tracks, tags
   - Deezer   -> artist images (free, no auth required)
===============================================================================
"""

from http.server import BaseHTTPRequestHandler
import json
import os
import urllib.parse
import urllib.request
import urllib.error


# =========================================================================
# Configuration
# =========================================================================
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
SIMILAR_PER_ARTIST = 10
MAX_RECOMMENDED_ARTISTS = 25
MAX_INPUT_ARTISTS = 30

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Placeholder image when no real image is found (data URI - inline SVG gradient)
PLACEHOLDER_IMAGE = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'>"
    "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
    "<stop offset='0' stop-color='%23a855f7'/><stop offset='1' stop-color='%23ec4899'/>"
    "</linearGradient></defs>"
    "<rect width='200' height='200' fill='url(%23g)'/>"
    "<text x='100' y='115' font-size='80' text-anchor='middle' fill='white' "
    "font-family='Arial'>%E2%99%AA</text></svg>"
)


# =========================================================================
# HTTP helper
# =========================================================================

def _http_get_json(url, timeout=8):
    """GET request that returns parsed JSON, or None on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None


# =========================================================================
# Last.fm helpers
# =========================================================================

def lastfm_request(method, params, timeout=10):
    base = "http://ws.audioscrobbler.com/2.0/"
    params = {**params, "method": method, "api_key": LASTFM_API_KEY, "format": "json"}
    url = base + "?" + urllib.parse.urlencode(params)
    return _http_get_json(url, timeout=timeout)


def get_similar_artists(artist_name, limit=10):
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
        }
        for a in artists if a.get("name")
    ]


def get_top_tracks(artist_name, limit=3):
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
    data = lastfm_request("artist.getTopTags", {"artist": artist_name, "autocorrect": 1})
    if not data or "toptags" not in data:
        return []
    tags = data["toptags"].get("tag", [])
    return [t.get("name", "") for t in tags[:limit] if t.get("name")]


# =========================================================================
# Deezer helper - free, no auth required
# =========================================================================

# Per-process cache so we don't hit Deezer twice for the same artist
_deezer_cache = {}


def get_deezer_image(artist_name):
    """
    Returns the highest-quality artist image available from Deezer.
    Tries picture_xl > picture_big > picture_medium > picture.
    Returns None on failure (caller should use placeholder).
    """
    if not artist_name:
        return None

    key = artist_name.lower()
    if key in _deezer_cache:
        return _deezer_cache[key]

    url = (
        "https://api.deezer.com/search/artist?limit=1&q="
        + urllib.parse.quote(artist_name)
    )
    data = _http_get_json(url, timeout=6)

    image = None
    if data and isinstance(data.get("data"), list) and data["data"]:
        artist = data["data"][0]
        # Skip Deezer's generic placeholder (URLs like ".../artist//..." or "/images/artist//")
        for size_field in ("picture_xl", "picture_big", "picture_medium", "picture"):
            candidate = artist.get(size_field)
            if candidate and "/artist//" not in candidate:
                image = candidate
                break

    _deezer_cache[key] = image
    return image


# =========================================================================
# Helper: locate index.html on the Vercel filesystem
# =========================================================================

def _read_index_html():
    possible_paths = [
        "index.html",
        "../index.html",
        "/var/task/index.html",
        "/vercel/path0/index.html",
        os.path.join(os.path.dirname(__file__), "..", "index.html"),
    ]
    for path in possible_paths:
        try:
            with open(path, "rb") as f:
                return f.read()
        except (FileNotFoundError, OSError):
            continue
    return None


# =========================================================================
# Main handler
# =========================================================================

class handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        """Serves index.html for any GET request."""
        html_content = _read_index_html()
        if html_content is None:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"index.html not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(html_content)

    def do_POST(self):
        # ---- 1. Validate request ----
        if not LASTFM_API_KEY:
            self._send(500, {"error": "Server misconfiguration: API key missing"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 10000:
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

        # ---- 2. Sanitize input ----
        clean_artists = []
        for a in artists[:MAX_INPUT_ARTISTS]:
            if isinstance(a, str):
                name = a.strip()[:100]
                if name:
                    clean_artists.append(name)

        if not clean_artists:
            self._send(400, {"error": "No valid artist names"})
            return

        # ---- 3. Aggregate similar artists ----
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
                        "sources": [base_artist],
                    }

        if not artist_scores:
            self._send(200, {"recommendations": [], "message": "No similar artists found"})
            return

        # ---- 4. Sort and pick top ----
        top = sorted(artist_scores.items(), key=lambda x: x[1]["score"], reverse=True)
        top = top[:MAX_RECOMMENDED_ARTISTS]

        # ---- 5. Enrich top artists with images, tracks, tags ----
        recommendations = []
        for i, (name, info) in enumerate(top):
            # Deezer image - with placeholder fallback
            image = get_deezer_image(name) or PLACEHOLDER_IMAGE

            entry = {
                "name": name,
                "score": round(info["score"], 2),
                "lastfm_url": info["url"],
                "image": image,
                "similar_to": info["sources"],
                "spotify_search": f"https://open.spotify.com/search/{urllib.parse.quote(name)}",
                "soundcloud_search": f"https://soundcloud.com/search?q={urllib.parse.quote(name)}",
            }
            # Top 10 also get tracks and tags
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
