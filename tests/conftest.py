"""Tests run the merchant node the way run_demo.py does: demo controls on, and Stripe TEST-format keys
present (the SDK is faked in every test; nothing touches the network)."""
import os

os.environ.setdefault("DEMO_CONTROLS", "1")
os.environ.setdefault("SPECIALIST_WEIGHTS", "none")  # existing tests assert exact verdicts; specialist tests turn them on
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_offline")
os.environ.setdefault("STRIPE_PUBLISHABLE_KEY", "pk_test_offline")
