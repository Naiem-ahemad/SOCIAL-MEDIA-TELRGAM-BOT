import os
import time
import asyncio
import json
from collections import deque, defaultdict
from typing import Optional, Tuple, List

from dotenv import load_dotenv
from google import genai

from core.utils import db, logger

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

DEFAULT_BURST_LIMIT = 5
DEFAULT_BURST_WINDOW = 10
DEFAULT_SUSTAINED_LIMIT = 80
DEFAULT_SUSTAINED_WINDOW = 60
DEFAULT_BAN_HOURS = 24

class RateLimiter:
    """Async in-memory per-user rate limiter with DB-backed bans and optional Gemini reason generation.

    Usage:
      from core.rate_limiter import rate_limiter
      ok, reason = await rate_limiter.record_activity(user_id)
      if not ok:
          # user banned; reason explains why
    """

    def __init__(
        self,
        burst_limit: int = DEFAULT_BURST_LIMIT,
        burst_window: int = DEFAULT_BURST_WINDOW,
        sustained_limit: int = DEFAULT_SUSTAINED_LIMIT,
        sustained_window: int = DEFAULT_SUSTAINED_WINDOW,
        ban_hours: int = DEFAULT_BAN_HOURS,
    ):
        self.burst_limit = burst_limit
        self.burst_window = burst_window
        self.sustained_limit = sustained_limit
        self.sustained_window = sustained_window
        self.ban_hours = ban_hours

        self._activities: dict[int, deque] = defaultdict(deque)
        self._lock = asyncio.Lock()

        # Gemini settings
        self.gemini_enabled = bool(GEMINI_API_KEY)
        if not self.gemini_enabled:
            logger.info("Gemini disabled (missing GEMINI_API_KEY)", platform="RATE_LIMIT")

    async def is_banned(self, user_id: int) -> bool:
        return await asyncio.to_thread(db.is_banned, user_id)

    async def get_ban_reason(self, user_id: int) -> Optional[str]:
        user = await asyncio.to_thread(db.get_user, user_id)
        if user:
            return user.get("reason")
        return None

    async def _generate_reason_with_gemini(self, user_id: int, recent_ts: List[float], burst_count: int, sustained_count: int) -> str:
        """Call Gemini (if enabled) to generate a concise ban reason/report.

        If Gemini is not enabled or fails, returns a fallback explanatory string.
        """
        prompt = (
            f"You are a security analyst. User {user_id} triggered a rate-limit. "
            f"Recent activity timestamps (unix): {recent_ts[-20:]}\n"
            f"Burst window count={burst_count}, sustained window count={sustained_count}.\n"
            "Provide a 1-2 sentence human-readable reason for banning and suggest a ban duration in hours."
        )

        if not self.gemini_enabled:
            # fallback summary
            return f"rate_limit: burst={burst_count}, sustained={sustained_count}"

        try:
            # Use google.genai client; run in thread to avoid blocking event loop
            def call_genai():
                client = genai.Client()
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                )
                return getattr(response, "text", str(response))

            text = await asyncio.to_thread(call_genai)
            if not text:
                return f"rate_limit: burst={burst_count}, sustained={sustained_count}"
            return str(text).strip()[:2000]

        except Exception as e:
            logger.warning(f"Gemini (genai) call failed: {e}", platform="RATE_LIMIT")
            return f"rate_limit: burst={burst_count}, sustained={sustained_count}"

    async def record_activity(self, user_id: int) -> Tuple[bool, Optional[str]]:
        """Record activity and return (allowed, reason).

        If the user is auto-banned, reason will contain the generated reason.
        """
        now = time.time()
        async with self._lock:
            dq = self._activities[user_id]
            dq.append(now)

            # prune older than the largest window
            cutoff = now - max(self.sustained_window, self.burst_window)
            while dq and dq[0] < cutoff:
                dq.popleft()

            burst_cutoff = now - self.burst_window
            burst_count = sum(1 for t in dq if t >= burst_cutoff)

            sustained_cutoff = now - self.sustained_window
            sustained_count = sum(1 for t in dq if t >= sustained_cutoff)

            if burst_count >= self.burst_limit or sustained_count >= self.sustained_limit:
                # generate reason (attempt Gemini)
                recent_ts = list(dq)
                reason = await self._generate_reason_with_gemini(user_id, recent_ts, burst_count, sustained_count)

                # persist ban
                await asyncio.to_thread(db.ban_user, user_id, reason, self.ban_hours)
                logger.warning(f"Auto-banned {user_id}: {reason}", platform="RATE_LIMIT")
                return False, reason
                
        return True, None

# module-level shared instance
rate_limiter = RateLimiter()

async def check_and_record_user_activity(user_id: int) -> Tuple[bool, Optional[str]]:
    """Convenience helper for handlers.

    Returns (allowed, reason). If not allowed, reason explains ban.
    """
    if await rate_limiter.is_banned(user_id):
        reason = await rate_limiter.get_ban_reason(user_id)
        logger.warning(f"Blocked request from banned user {user_id}: {reason}", platform="RATE_LIMIT")
        return False, reason or "banned"

    return await rate_limiter.record_activity(user_id)
