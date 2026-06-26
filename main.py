from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import redis.asyncio as aioredis
import os

# ── Config ────────────────────────────────────────────────────────────────────

SESSION_PREFIX = os.getenv("SESSION_PREFIX", "_oauth2_proxy-")
META_TTL       = int(os.getenv("META_TTL", "604800"))  # 7 days

# ── Lifespan ──────────────────────────────────────────────────────────────────

redis_client: aioredis.Redis = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    redis_client = aioredis.from_url(
        os.getenv("REDIS_URL", "redis://redis:6379"),
        decode_responses=True,
    )
    await redis_client.ping()
    yield
    await redis_client.aclose()

app = FastAPI(title="OAuth2 Session Manager", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def format_ttl(seconds: int) -> str:
    if seconds < 0:
        return "persistent"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


async def scan_keys(pattern: str) -> list[str]:
    """SCAN instead of KEYS — non-blocking regardless of keyspace size."""
    keys   = []
    cursor = 0
    while True:
        cursor, batch = await redis_client.scan(cursor, match=pattern, count=100)
        keys.extend(batch)
        if cursor == 0:
            break
    return keys

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/admin-portal")
async def serve_ui():
    return FileResponse("/app/index.html")


@app.get("/api/sessions")
async def list_sessions():
    try:
        ticket_keys = await scan_keys(f"{SESSION_PREFIX}*")
        if not ticket_keys:
            return []

        handles = [k.removeprefix(SESSION_PREFIX) for k in ticket_keys]

        # Batch-fetch TTL + meta + destinations for all handles in one pipeline.
        async with redis_client.pipeline(transaction=False) as pipe:
            for key in ticket_keys:
                pipe.ttl(key)
            for h in handles:
                pipe.hgetall(f"session_meta:{h}")
            for h in handles:
                pipe.hgetall(f"session_destinations:{h}")
            results = await pipe.execute()

        n         = len(handles)
        ttls      = results[:n]
        metas     = results[n : n * 2]
        dest_maps = results[n * 2 :]

        # Batch-check blocklist for all unique user IDs in a second pipeline.
        user_ids = list({m.get("user_id") for m in metas if m.get("user_id")})
        if user_ids:
            async with redis_client.pipeline(transaction=False) as pipe:
                for uid in user_ids:
                    pipe.exists(f"blocklist:{uid}")
                block_results = await pipe.execute()
            blocked_set = {uid for uid, hit in zip(user_ids, block_results) if hit}
        else:
            blocked_set = set()

        sessions = []
        for handle, ttl, meta, dest_map in zip(handles, ttls, metas, dest_maps):
            user_id      = meta.get("user_id")
            destinations = sorted(
                [{"destination": d, "last_seen": t} for d, t in dest_map.items()],
                key=lambda x: x["last_seen"],
                reverse=True,
            )
            sessions.append({
                "handle":       handle,
                "user_id":      user_id,
                "email":        meta.get("email"),
                "ip":           meta.get("ip"),
                "device":       meta.get("device"),
                "destinations": destinations,
                "note":         meta.get("note"),
                "last_seen":    meta.get("last_seen"),
                "ttl_seconds":  ttl,
                "expires_in":   format_ttl(ttl),
                "blocked":      user_id in blocked_set,
            })

        return sorted(sessions, key=lambda s: s.get("last_seen") or "", reverse=True)

    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Redis error: {e}")


@app.delete("/api/sessions/{handle}")
async def revoke_session(handle: str):
    meta    = await redis_client.hgetall(f"session_meta:{handle}")
    user_id = meta.get("user_id")

    async with redis_client.pipeline(transaction=False) as pipe:
        pipe.delete(f"{SESSION_PREFIX}{handle}")
        pipe.delete(f"session_meta:{handle}")
        pipe.delete(f"session_destinations:{handle}")
        if user_id:
            pipe.srem(f"user_sessions:{user_id}", handle)
        await pipe.execute()

    return {"revoked": True, "handle": handle}


@app.post("/api/sessions/{handle}/block")
async def block_user(handle: str):
    meta    = await redis_client.hgetall(f"session_meta:{handle}")
    user_id = meta.get("user_id")
    if not user_id:
        raise HTTPException(status_code=404, detail="User mapping not found")

    # Permanent until explicitly removed — no silent TTL expiry.
    await redis_client.set(f"blocklist:{user_id}", "1")
    return {"blocked": True, "user_id": user_id}


@app.delete("/api/sessions/{handle}/block")
async def unblock_user(handle: str):
    meta    = await redis_client.hgetall(f"session_meta:{handle}")
    user_id = meta.get("user_id")
    if not user_id:
        raise HTTPException(status_code=404, detail="User mapping not found")

    await redis_client.delete(f"blocklist:{user_id}")
    return {"unblocked": True, "user_id": user_id}


@app.get("/api/stats")
async def stats():
    try:
        ticket_keys  = await scan_keys(f"{SESSION_PREFIX}*")
        meta_keys    = await scan_keys("session_meta:*")
        blocked_keys = await scan_keys("blocklist:*")
        orphan_count = len(meta_keys) - len(ticket_keys)
        return {
            "total_tickets": len(ticket_keys),
            "known_users":   len(meta_keys),
            "blocked_users": len(blocked_keys),
            "orphaned_meta": max(orphan_count, 0),
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Redis error: {e}")


@app.get("/healthz")
async def health():
    await redis_client.ping()
    return {"status": "ok"}


@app.post("/api/sessions/{handle}/note")
async def update_note(handle: str, note_data: dict):
    note = str(note_data.get("note", ""))[:500]
    try:
        await redis_client.hset(f"session_meta:{handle}", "note", note)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Redis error: {e}")
    return {"status": "ok"}
