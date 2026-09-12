"""External integrations (SMS notifications via Twilio)."""

from services.twilio_service import TwilioService, get_twilio_service

__all__ = ["TwilioService", "get_twilio_service"]