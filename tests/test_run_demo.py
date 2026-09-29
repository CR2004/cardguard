"""run_demo.py's reviewer credential: a preset REVIEWER_TOKEN (>= 8 chars) is honoured so a demo value
survives restarts; a trivial one is refused; unset means a fresh random token every start."""
import pytest

import run_demo


def test_preset_reviewer_token_is_used():
    assert run_demo.reviewer_token({"REVIEWER_TOKEN": "demo-review-2026"}) == ("demo-review-2026", True)


def test_short_reviewer_token_is_refused():
    with pytest.raises(ValueError):
        run_demo.reviewer_token({"REVIEWER_TOKEN": "1234"})


def test_unset_reviewer_token_is_minted_fresh():
    a, preset_a = run_demo.reviewer_token({})
    b, _ = run_demo.reviewer_token({"REVIEWER_TOKEN": "   "})
    assert not preset_a and len(a) == 16 and a != b and a.isalnum()
