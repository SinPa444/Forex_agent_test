"""
llm_utils.py
============
ابزارهای کمکی برای فراخوانی LLM، شامل مکانیزم Retry-on-parse-failure و پاکسازی Markdown.
"""

import logging
import re
import time
from typing import Any

from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)

def _strip_json_markdown(text: Any) -> str:
    """
    Remove markdown code blocks around JSON if present.
    Some LLMs wrap JSON output in ```json ... ``` which breaks Pydantic parsing.
    """
    if isinstance(text, BaseMessage):
        text = text.content
    if isinstance(text, str):
        match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if match:
            return match.group(1)
        if text.strip().startswith("```") and text.strip().endswith("```"):
            text = text.strip()[3:-3].strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
            return text
    return text


def invoke_with_retry(chain, inputs, max_retries: int = 2, initial_delay: float = 1.0):
    """
    فراخوانی یک LangChain chain با منطق تلاش مجدد (Retry) برای خطاهای پارس و اعتبارسنجی.
    """
    last_exc = None
    delay = initial_delay
    
    for attempt in range(max_retries + 1):
        try:
            result = chain.invoke(inputs)
            if attempt > 0:
                logger.info("LLM invoke succeeded on attempt %d/%d", attempt + 1, max_retries + 1)
            return result
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "LLM invoke failed (attempt %d/%d): %s",
                attempt + 1, max_retries + 1, str(exc)[:200]
            )
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                logger.error("LLM invoke failed after %d attempts. Giving up.", max_retries + 1)
                
    raise last_exc