"""Chat with the writer model (Ollama). The writer is a story partner: it works on the user's story with them, and
never changes it behind their back. When they are happy, the app takes the story text into a new project."""
from typing import List, Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.http_retry import post_with_retry
from app.services.llm_service import LLMService

router = APIRouter()

SYSTEM = (
    "You are the writing partner of a short-film maker. You help the user develop THEIR story: answer questions, suggest "
    "ideas, fix plot holes, tighten dialogue, describe characters and places. Rules: (1) The user's story belongs to the "
    "user. Never change names, events, order or the ending unless they ask you to - and when they ask for a change, change "
    "only that and say what you changed. (2) Offer ideas as options, not as rewrites. (3) When the user asks for the story "
    "written out, or for the final version, reply with the full story text only, in plain paragraphs, no commentary, so it "
    "can be used as it is. (4) Be brief and concrete; ask at most one question at a time."
)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=20000)


class ChatRequest(BaseModel):
    messages: List[ChatMessage] = Field(..., min_length=1, max_length=60)


class ChatReply(BaseModel):
    reply: str


@router.post("/chat", response_model=ChatReply)
async def chat(payload: ChatRequest):
    try:
        llm = LLMService()
    except Exception as e:  # noqa: BLE001 - writer not configured / notebook off
        raise HTTPException(status_code=503, detail=f"The writer model is not available: {e}")
    messages = [{"role": "system", "content": SYSTEM}] + [m.model_dump() for m in payload.messages]
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await post_with_retry(
                client, f"{llm.ollama_url}/api/chat",
                json={"model": llm.model_name, "messages": messages, "stream": False},
                headers=llm._HEADERS,
            )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not reach the writer model: {e}")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"The writer model answered {r.status_code}: {r.text[:200]}")
    text = ((r.json().get("message") or {}).get("content") or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="The writer model sent an empty answer. Try again.")
    return ChatReply(reply=text)
