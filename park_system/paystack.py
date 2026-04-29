import requests
import uuid
from django.conf import settings

HEADERS = {
    "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
    "Content-Type": "application/json",
}


def generate_reference():
    """Unique reference for every transaction."""
    return f"PARK-{uuid.uuid4().hex[:12].upper()}"


def initialize_transaction(email, amount_naira, reference, metadata=None, callback_url=None):
    """
    Initiate a payment with Paystack.
    amount_naira: amount in naira — we convert to kobo here.
    Returns the full Paystack response dict.
    """
    payload = {
        "email": email,
        "amount": amount_naira * 100,  # convert to kobo
        "reference": reference,
        "metadata": metadata or {},
    }
    if callback_url:
        payload["callback_url"] = callback_url

    response = requests.post(
        f"{settings.PAYSTACK_BASE_URL}/transaction/initialize",
        json=payload,
        headers=HEADERS,
        timeout=10,
    )
    return response.json()


def verify_transaction(reference):
    """
    Verify a transaction by reference.
    Call this in both the callback and webhook to confirm payment.
    Returns the full Paystack response dict.
    """
    response = requests.get(
        f"{settings.PAYSTACK_BASE_URL}/transaction/verify/{reference}",
        headers=HEADERS,
        timeout=10,
    )
    return response.json()