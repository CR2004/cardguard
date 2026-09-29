"""Tests run the merchant node the way run_demo.py does: demo controls on (chosen country, hour, attacks)."""
import os

os.environ.setdefault("DEMO_CONTROLS", "1")
os.environ.setdefault("SPECIALIST_WEIGHTS", "none")  # existing tests assert exact verdicts; specialist tests turn them on
