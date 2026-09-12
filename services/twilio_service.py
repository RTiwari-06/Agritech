"""Twilio SMS notifications.

Safe to import and call anywhere: when Twilio is not configured the service
returns a structured ``unsent`` result instead of raising, so the rest of the
framework stays operational while credentials are absent.
"""

from typing import Dict, Optional

from config import Config


class TwilioService:
    def __init__(
        self,
        account_sid: Optional[str] = None,
        auth_token: Optional[str] = None,
        from_number: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self.account_sid = account_sid or Config.TWILIO_ACCOUNT_SID
        self.auth_token = auth_token or Config.TWILIO_AUTH_TOKEN
        self.from_number = from_number or Config.TWILIO_FROM_NUMBER
        self.enabled = enabled if enabled is not None else Config.TWILIO_ENABLED
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from twilio.rest import Client

            self._client = Client(self.account_sid, self.auth_token)
        return self._client

    def configured(self) -> bool:
        return Config.twilio_ready() and bool(
            self.account_sid and self.auth_token and self.from_number
        )

    def send_sms(self, to: str, body: str) -> Dict:
        """Send a plain SMS. Never raises for configuration issues."""
        if not self.configured():
            return {
                "delivered": False,
                "reason": "twilio not configured or disabled: set TWILIO_ENABLED=true plus SID/auth/from",
                "sid": None,
            }
        try:
            message = self.client.messages.create(
                body=body, from_=self.from_number, to=to
            )
            return {"delivered": True, "reason": None, "sid": message.sid}
        except Exception as error:  # network / provider errors reported, not swallowed
            return {"delivered": False, "reason": str(error), "sid": None}

    def notify_seller_sale(
        self, to: str, product_name: str, quantity: float, total_price: float
    ) -> Dict:
        body = (
            f"Sale confirmed! {quantity:g} x {product_name} "
            f"(total ${total_price:.2f}). Great job!"
        )
        return self.send_sms(to=to, body=body)

    def send_order_confirmation(
        self, to: str, product_name: str, quantity: float, total_price: float
    ) -> Dict:
        body = (
            f"Order confirmed: {quantity:g} x {product_name} "
            f"for ${total_price:.2f}. Track it in the marketplace."
        )
        return self.send_sms(to=to, body=body)


_service_instance: Optional[TwilioService] = None


def get_twilio_service() -> TwilioService:
    """Return a singleton, lazily constructed TwilioService."""
    global _service_instance
    if _service_instance is None:
        _service_instance = TwilioService()
    return _service_instance