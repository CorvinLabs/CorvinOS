"""
Voice Session Persistence (Phase 2a)
SQLAlchemy models for storing voice recordings, transcripts, summaries

Type-Aware Summary Support:
- code: syntax-aware summary (function names, key logic)
- image: visual-aware summary (objects, colors, composition)
- video: temporal-aware summary (scenes, transitions, key moments)
- text: content-aware summary (topics, entities, sentiment)

@date 2026-09-25
@phase Phase 2a: Persistent Storage + Type-Aware Detection
"""

from sqlalchemy import Column, String, Text, DateTime, Float, Enum, Integer, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
from enum import Enum as PyEnum
import uuid

Base = declarative_base()


class MessageType(str, PyEnum):
    """Message content type (for type-aware summary strategies)"""
    TEXT = "text"
    CODE = "code"
    IMAGE = "image"
    VIDEO = "video"
    MIXED = "mixed"  # Contains multiple types


class SummaryStrategy(str, PyEnum):
    """Summary generation strategy based on content type"""
    SIMPLE = "simple"  # Phase 1: LLM summary only
    SYNTAX_AWARE = "syntax_aware"  # Phase 2: Code structure preserved
    VISUAL_AWARE = "visual_aware"  # Phase 2: Image objects/composition
    TEMPORAL_AWARE = "temporal_aware"  # Phase 2: Video scenes/timeline
    ENTITY_AWARE = "entity_aware"  # Phase 2: Named entities, relations


class VoiceSession(Base):
    """
    Persistent voice recording session.

    Lifecycle:
    1. User starts recording (status=RECORDING)
    2. Chat messages flow in
    3. User stops recording (status=STOPPED)
    4. Summary auto-generated (summary_generated_at filled)
    5. Optional: User provides feedback (feedback_score set)
    """
    __tablename__ = "voice_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(64), nullable=False, index=True)
    session_id = Column(String(128), nullable=False, index=True)  # User-facing ID

    # Status & Lifecycle
    status = Column(String(32), nullable=False, default="RECORDING")  # RECORDING, STOPPED, ERROR
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    stopped_at = Column(DateTime, nullable=True)

    # Session Metadata
    owner_id = Column(String(64), nullable=False)  # User who created session
    agent_invited = Column(String(64), nullable=True)  # Agent ID if invited

    # Content Detection
    primary_message_type = Column(Enum(MessageType), nullable=False, default=MessageType.TEXT)
    has_code = Column(Integer, default=0)  # Count of code messages
    has_images = Column(Integer, default=0)  # Count of image messages
    has_videos = Column(Integer, default=0)  # Count of video messages

    # Raw Data
    transcript = Column(Text, nullable=True)  # Speech-to-text transcript
    transcript_confidence = Column(Float, nullable=True)  # STT confidence score

    # Summary (Phase 2)
    summary = Column(Text, nullable=True)  # Auto-generated summary
    summary_strategy = Column(Enum(SummaryStrategy), nullable=True, default=SummaryStrategy.SIMPLE)
    summary_generated_at = Column(DateTime, nullable=True)
    summary_model = Column(String(64), nullable=True)  # Which model generated it

    # Learning Loop (Phase 2c)
    feedback_score = Column(Float, nullable=True)  # User rating of summary quality (0-1)
    feedback_provided_at = Column(DateTime, nullable=True)
    feedback_comment = Column(Text, nullable=True)

    # Audit Trail
    messages_count = Column(Integer, default=0)
    audio_duration_seconds = Column(Float, nullable=True)

    # Relationships
    messages = relationship("VoiceMessage", back_populates="session", cascade="all, delete-orphan")
    type_detections = relationship("MessageTypeDetection", back_populates="session", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<VoiceSession {self.session_id} ({self.status})>"


class VoiceMessage(Base):
    """
    Individual message in a voice session.
    Stores transcript segment + detected type for type-aware summarization.
    """
    __tablename__ = "voice_messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(128), ForeignKey("voice_sessions.session_id"), nullable=False)

    # Message Content
    author_kind = Column(String(32), nullable=False)  # "human", "agent", "system"
    author_name = Column(String(64), nullable=False)
    text = Column(Text, nullable=False)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Type Detection (for type-aware summary)
    detected_type = Column(Enum(MessageType), nullable=False, default=MessageType.TEXT)
    type_confidence = Column(Float, nullable=True)

    # Optional Metadata (for detected types)
    metadata = Column(JSON, nullable=True)  # e.g., {"language": "python", "functions": 5} for code

    # Relationships
    session = relationship("VoiceSession", back_populates="messages")

    def __repr__(self):
        return f"<VoiceMessage {self.detected_type} from {self.author_name}>"


class MessageTypeDetection(Base):
    """
    Type detection result for a message (for Phase 2: learning type-awareness).
    """
    __tablename__ = "message_type_detections"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(128), ForeignKey("voice_sessions.session_id"), nullable=False)
    message_index = Column(Integer, nullable=False)  # Position in session

    # Detection Results
    detected_type = Column(Enum(MessageType), nullable=False)
    confidence_scores = Column(JSON, nullable=False)  # {TEXT: 0.8, CODE: 0.15, ...}
    detection_model = Column(String(64), nullable=False)  # Which model detected it

    # Feedback (Phase 2c: learning)
    is_correct = Column(Integer, nullable=True)  # 1 = correct, 0 = wrong

    # Relationships
    session = relationship("VoiceSession", back_populates="type_detections")

    def __repr__(self):
        return f"<MessageTypeDetection {self.detected_type} (confidence: {self.confidence_scores.get(self.detected_type, 0)})>"
