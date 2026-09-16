"""Natural Voice Integration (ADR-0847) — k=4 PRODUCTION

Streaming voice with interruption support, contextual awareness.
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Optional, AsyncGenerator, Dict, List
from datetime import datetime
from enum import Enum


class VoiceStreamType(Enum):
    STT_PARTIAL = "stt_partial"
    STT_FINAL = "stt_final"
    TTS_CHUNK = "tts_chunk"
    INTERRUPT = "interrupt"


@dataclass
class VoiceContext:
    """Short-term memory for implicit reference resolution."""
    chat_id: str
    last_3_turns: List[Dict] = None
    tone_detected: str = "neutral"
    
    def __post_init__(self):
        if self.last_3_turns is None:
            self.last_3_turns = []


class NaturalVoiceIntegration:
    """Production voice integration with streaming + interruption."""
    
    name = "voice_integration"
    version = "1.0.0"
    
    def __init__(self):
        self.streaming_active = False
        self.stream_task = None
        self.context_memory: Dict[str, VoiceContext] = {}
    
    async def stream_response(
        self, 
        text: str, 
        chat_id: str,
        voice_speed: float = 1.0
    ) -> AsyncGenerator:
        """Stream TTS response in real-time chunks."""
        if not self.context_memory.get(chat_id):
            self.context_memory[chat_id] = VoiceContext(chat_id=chat_id)
        
        self.streaming_active = True
        context = self.context_memory[chat_id]
        
        try:
            # Split text into phrases (sentence-like chunks)
            phrases = self._chunk_text_smartly(text)
            
            for phrase in phrases:
                if not self.streaming_active:
                    # Interrupted
                    yield {
                        "type": VoiceStreamType.INTERRUPT.value,
                        "timestamp": datetime.utcnow().isoformat() + "Z"
                    }
                    break
                
                # Simulate TTS chunk (replace with real TTS service)
                duration_ms = max(100, int(len(phrase) * 50 / voice_speed))
                
                yield {
                    "type": VoiceStreamType.TTS_CHUNK.value,
                    "text": phrase,
                    "duration_ms": duration_ms,
                    "timestamp": datetime.utcnow().isoformat() + "Z"
                }
                
                await asyncio.sleep(duration_ms / 1000.0)
            
            # Record turn in context for next implicit reference
            context.last_3_turns.append({
                "role": "assistant",
                "content": text,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            })
            if len(context.last_3_turns) > 6:  # Keep 3 user + 3 assistant turns
                context.last_3_turns.pop(0)
        
        finally:
            self.streaming_active = False
    
    async def interrupt(self, chat_id: str) -> None:
        """Cancel mid-response (user interrupted)."""
        self.streaming_active = False
        yield {
            "type": VoiceStreamType.INTERRUPT.value,
            "chat_id": chat_id,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
    
    async def resolve_implicit_reference(self, query: str, chat_id: str) -> str:
        """Resolve 'that thing' / 'last one' to actual context."""
        if chat_id not in self.context_memory:
            return query
        
        context = self.context_memory[chat_id]
        
        # Simple pattern matching for implicit references
        implicit_markers = ["that thing", "last one", "same as before", "like that"]
        
        if any(marker in query.lower() for marker in implicit_markers):
            if context.last_3_turns:
                last_assistant_msg = next(
                    (t for t in reversed(context.last_3_turns) if t["role"] == "assistant"),
                    None
                )
                if last_assistant_msg:
                    return f"{query} (Reference: {last_assistant_msg['content'][:100]}...)"
        
        return query
    
    async def record_user_turn(self, text: str, chat_id: str, tone: Optional[str] = None) -> None:
        """Record user STT input + detected tone."""
        if chat_id not in self.context_memory:
            self.context_memory[chat_id] = VoiceContext(chat_id=chat_id)
        
        context = self.context_memory[chat_id]
        
        context.last_3_turns.append({
            "role": "user",
            "content": text,
            "tone": tone or "neutral",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        })
        
        if tone:
            context.tone_detected = tone
        
        if len(context.last_3_turns) > 6:
            context.last_3_turns.pop(0)
    
    def _chunk_text_smartly(self, text: str, chunk_size: int = 50) -> List[str]:
        """Split text into natural phrase chunks."""
        # Split by sentence, then by phrase if too long
        sentences = text.replace("! ", "!|").replace("? ", "?|").split("|")
        
        chunks = []
        current_chunk = ""
        
        for sentence in sentences:
            if len(current_chunk) + len(sentence) <= chunk_size:
                current_chunk += sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = sentence
        
        if current_chunk:
            chunks.append(current_chunk)
        
        return chunks or [text]
    
    def get_context(self, chat_id: str) -> Optional[VoiceContext]:
        """Expose context for integration with other subsystems."""
        return self.context_memory.get(chat_id)
    
    def clear_context(self, chat_id: str) -> None:
        """Clear context when conversation ends."""
        self.context_memory.pop(chat_id, None)
