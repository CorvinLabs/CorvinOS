"""Exceptions for Video Producer Skill 2.0."""


class VideoProducerError(Exception):
    """Base exception for video producer."""
    pass


class AssetIngestionError(VideoProducerError):
    """Raised when asset ingestion fails."""
    pass


class AnalysisIncompleteError(VideoProducerError):
    """Raised when analysis gates are not met."""
    pass


class AnalysisGateFailedError(AnalysisIncompleteError):
    """Raised when ready_for_narration == false."""
    pass


class PreconditionNotMetError(VideoProducerError):
    """Raised when a worker precondition is not satisfied."""
    pass


class StoryboardValidationError(VideoProducerError):
    """Raised when storyboard validation fails."""
    pass
