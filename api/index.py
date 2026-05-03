"""
===============================================================================
 Music Recommender - Vercel Serverless Function
===============================================================================
 Endpoints:
   GET  /                       -> serves index.html
   POST /api/recommendations    -> returns artist recommendations
   POST /api/auth/verify        -> verifies Google ID token, returns user info
   GET  /api/favorites          -> returns favorites list (auth required)
   POST /api/favorites/add      -> adds an artist to favorites (auth required)
   POST /api/favorites/remove   -> removes an artist from favorites (auth required)

 Sources:
   - Last.fm  -> similar artists, top tracks, tags
   - Deezer   -> artist images (free, no auth required)
   - Upstash Redis (KV) -> per-user favorites storage
   - Google Identity Services -> user authentication

 Auth model:
   - Frontend uses Google Identity Services to get an ID Token (JWT)
   - All authenticated requests pass the token in the "Authorization" header:
       Authorization: Bearer <id_token>
   - Backend verifies the token by calling Google's tokeninfo endpoint
   - User is identified by the "sub" claim (a stable Google user ID)
===============================================================================
"""

from http.server import BaseHTTPRequestHandler
import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error


# =========================================================================
# Configuration
# =========================================================================
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

# Upstash KV (Vercel created these env vars with a "kv_" prefix)
KV_REST_API_URL = os.environ.get("kv_KV_REST_API_URL", "")
KV_REST_API_TOKEN = os.environ.get("kv_KV_REST_API_TOKEN", "")

SIMILAR_PER_ARTIST = 10
MAX_RECOMMENDED_ARTISTS = 25
MAX_INPUT_ARTISTS = 30
MAX_FAVORITES_PER_USER = 200

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Placeholder image (data URI - inline SVG gradient)
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
# HTTP helpers
# =========================================================================

def _http_get_json(url, timeout=8):
    """GET request that returns parsed JSON, or None on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None


def _http_request_json(method, url, headers=None, body=None, timeout=8):
    """Generic JSON HTTP request - used for Upstash REST API calls."""
    headers = headers or {}
    headers.setdefault("User-Agent", USER_AGENT)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, headers=headers, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
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

_deezer_cache = {}


def get_deezer_image(artist_name):
    """Returns the highest-quality Deezer image, or None on failure."""
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
        for size_field in ("picture_xl", "picture_big", "picture_medium", "picture"):
            candidate = artist.get(size_field)
            if candidate and "/artist//" not in candidate:
                image = candidate
                break

    _deezer_cache[key] = image
    return image


# =========================================================================
# Google OAuth - verify ID Token using Google's tokeninfo endpoint
# =========================================================================

def verify_google_id_token(id_token):
    """
    Verifies a Google ID Token by calling Google's tokeninfo endpoint.
    This is the simplest approach - no need for cryptography libraries.
    Returns the user info dict if valid, None otherwise.

    Validates:
    - Token signature (Google does this)
    - Token expiry
    - Audience matches our GOOGLE_CLIENT_ID
    - Issuer is google
    """
    if not id_token or not GOOGLE_CLIENT_ID:
        return None

    url = "https://oauth2.googleapis.com/tokeninfo?id_token=" + urllib.parse.quote(id_token)
    data = _http_get_json(url, timeout=6)
    if not data:
        return None

    # Check audience matches our client ID
    if data.get("aud") != GOOGLE_CLIENT_ID:
        return None

    # Check issuer
    iss = data.get("iss", "")
    if iss not in ("accounts.google.com", "https://accounts.google.com"):
        return None

    # Check expiry
    try:
        exp = int(data.get("exp", 0))
        if exp < time.time():
            return None
    except (ValueError, TypeError):
        return None

    # Email verified
    if data.get("email_verified") not in (True, "true"):
        return None

    return {
        "sub": data.get("sub", ""),       # stable Google user ID
        "email": data.get("email", ""),
        "name": data.get("name", ""),
        "picture": data.get("picture", ""),
    }


def _extract_bearer_token(auth_header):
    """Extracts the token from 'Authorization: Bearer <token>' header."""
    if not auth_header:
        return None
    parts = auth_header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


# =========================================================================
# Upstash Redis helpers (HTTP REST API)
# =========================================================================

def _kv_request(*command_parts):
    """
    Sends a Redis command via Upstash REST API.
    Example: _kv_request("SMEMBERS", "fav:user_123")
    Returns the parsed result, or None on failure.
    """
    if not KV_REST_API_URL or not KV_REST_API_TOKEN:
        return None

    # Upstash REST API expects each command part as a separate URL segment
    url = KV_REST_API_URL.rstrip("/") + "/" + "/".join(
        urllib.parse.quote(str(p), safe="") for p in command_parts
    )
    headers = {"Authorization": f"Bearer {KV_REST_API_TOKEN}"}
    return _http_request_json("GET", url, headers=headers, timeout=6)


def kv_get_favorites(user_id):
    """Returns the user's favorites as a list of dicts. Empty list if none."""
    if not user_id:
        return []
    key = f"fav:{user_id}"
    result = _kv_request("GET", key)
    if not result or "result" not in result or not result["result"]:
        return []
    try:
        return json.loads(result["result"])
    except (ValueError, TypeError):
        return []


def kv_set_favorites(user_id, favorites_list):
    """Replaces the user's entire favorites list."""
    if not user_id:
        return False
    key = f"fav:{user_id}"
    payload = json.dumps(favorites_list, ensure_ascii=False)
    result = _kv_request("SET", key, payload)
    return bool(result and result.get("result") == "OK")


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
# Recommendation logic (extracted from POST handler)
# =========================================================================

def build_recommendations(input_artists):
    """Returns the recommendations payload for a list of input artist names."""
    artist_scores = {}
    known = {a.lower() for a in input_artists}

    for base_artist in input_artists:
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
        return {"recommendations": [], "message": "No similar artists found"}

    top = sorted(artist_scores.items(), key=lambda x: x[1]["score"], reverse=True)
    top = top[:MAX_RECOMMENDED_ARTISTS]

    recommendations = []
    for i, (name, info) in enumerate(top):
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
        if i < 10:
            entry["top_tracks"] = get_top_tracks(name, limit=3)
            entry["tags"] = get_artist_tags(name, limit=5)
        else:
            entry["top_tracks"] = []
            entry["tags"] = []
        recommendations.append(entry)

    return {
        "recommendations": recommendations,
        "total_found": len(artist_scores),
    }


# =========================================================================
# Main handler
# =========================================================================

class handler(BaseHTTPRequestHandler):
    # ----- low-level response helpers -----

    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def _read_json_body(self):
        """Reads and parses JSON body. Raises ValueError on bad input."""
        length = int(self.headers.get("Content-Length", 0))
        if length > 50000:
            raise ValueError("Request too large")
        body = self.rfile.read(length).decode("utf-8") if length else ""
        return json.loads(body) if body else {}

    def _authed_user(self):
        """
        Extracts and verifies the Google ID token from the Authorization header.
        Returns the user info dict, or None if not authenticated.
        """
        auth = self.headers.get("Authorization", "")
        token = _extract_bearer_token(auth)
        if not token:
            return None
        return verify_google_id_token(token)

    # ----- HTTP method handlers -----

    def do_OPTIONS(self):
        # CORS preflight
        self._send(204, {})

    def do_GET(self):
        """
        GET /api/favorites    -> returns favorites list for the authenticated user
        GET anything else     -> serves index.html
        """
        path = self.path.split("?", 1)[0]

        if path == "/api/favorites":
            user = self._authed_user()
            if not user:
                self._send(401, {"error": "Authentication required"})
                return
            favorites = kv_get_favorites(user["sub"])
            self._send(200, {"favorites": favorites, "count": len(favorites)})
            return

        # Serve index.html for any other GET
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
        """
        Routes POST requests by path.
        """
        path = self.path.split("?", 1)[0]

        # ----- POST /api/auth/verify -----
        if path == "/api/auth/verify":
            try:
                data = self._read_json_body()
            except (ValueError, json.JSONDecodeError):
                self._send(400, {"error": "Invalid JSON"})
                return

            id_token = (data.get("credential") or data.get("id_token") or "").strip()
            if not id_token:
                self._send(400, {"error": "Missing credential/id_token"})
                return

            user = verify_google_id_token(id_token)
            if not user:
                self._send(401, {"error": "Invalid or expired token"})
                return

            self._send(200, {
                "user": {
                    "id": user["sub"],
                    "email": user["email"],
                    "name": user["name"],
                    "picture": user["picture"],
                }
            })
            return

        # ----- POST /api/favorites/add -----
        if path == "/api/favorites/add":
            user = self._authed_user()
            if not user:
                self._send(401, {"error": "Authentication required"})
                return

            try:
                data = self._read_json_body()
            except (ValueError, json.JSONDecodeError):
                self._send(400, {"error": "Invalid JSON"})
                return

            artist = data.get("artist")
            if not isinstance(artist, dict) or not artist.get("name"):
                self._send(400, {"error": "Missing artist data"})
                return

            # Sanitize artist data - keep only known fields
            clean_artist = {
                "name": str(artist.get("name", ""))[:200],
                "image": str(artist.get("image", ""))[:500],
                "lastfm_url": str(artist.get("lastfm_url", ""))[:500],
                "spotify_search": str(artist.get("spotify_search", ""))[:500],
                "soundcloud_search": str(artist.get("soundcloud_search", ""))[:500],
                "added_at": int(time.time()),
            }

            favorites = kv_get_favorites(user["sub"])

            # Already in favorites? - just return current list
            if any(f.get("name", "").lower() == clean_artist["name"].lower()
                   for f in favorites):
                self._send(200, {"favorites": favorites, "added": False, "reason": "Already in favorites"})
                return

            # Enforce per-user limit
            if len(favorites) >= MAX_FAVORITES_PER_USER:
                self._send(400, {
                    "error": f"Favorites limit reached ({MAX_FAVORITES_PER_USER})",
                    "favorites": favorites,
                })
                return

            favorites.append(clean_artist)
            ok = kv_set_favorites(user["sub"], favorites)
            if not ok:
                self._send(500, {"error": "Failed to save favorites"})
                return
            self._send(200, {"favorites": favorites, "added": True})
            return

        # ----- POST /api/favorites/remove -----
        if path == "/api/favorites/remove":
            user = self._authed_user()
            if not user:
                self._send(401, {"error": "Authentication required"})
                return

            try:
                data = self._read_json_body()
            except (ValueError, json.JSONDecodeError):
                self._send(400, {"error": "Invalid JSON"})
                return

            name = (data.get("name") or "").strip()
            if not name:
                self._send(400, {"error": "Missing artist name"})
                return

            favorites = kv_get_favorites(user["sub"])
            new_favorites = [f for f in favorites
                             if f.get("name", "").lower() != name.lower()]
            if len(new_favorites) == len(favorites):
                # Nothing was removed
                self._send(200, {"favorites": favorites, "removed": False})
                return

            ok = kv_set_favorites(user["sub"], new_favorites)
            if not ok:
                self._send(500, {"error": "Failed to save favorites"})
                return
            self._send(200, {"favorites": new_favorites, "removed": True})
            return

        # ----- POST /api/recommendations  (default - unchanged behavior) -----
        if not LASTFM_API_KEY:
            self._send(500, {"error": "Server misconfiguration: API key missing"})
            return

        try:
            data = self._read_json_body()
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "Invalid JSON"})
            return

        artists = data.get("artists", [])
        if not isinstance(artists, list) or not artists:
            self._send(400, {"error": "Missing 'artists' list"})
            return

        # Sanitize input
        clean_artists = []
        for a in artists[:MAX_INPUT_ARTISTS]:
            if isinstance(a, str):
                name = a.strip()[:100]
                if name:
                    clean_artists.append(name)

        if not clean_artists:
            self._send(400, {"error": "No valid artist names"})
            return

        payload = build_recommendations(clean_artists)
        self._send(200, payload)
