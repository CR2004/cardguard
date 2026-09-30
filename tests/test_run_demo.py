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


def test_importing_run_demo_does_not_load_the_dotenv_file():
    """The suite must stay offline: run_demo loads .env only inside main(), never on import."""
    import inspect
    src = inspect.getsource(run_demo)
    assert src.count("_dotenv.load()") == 1 and "_dotenv.load()" in inspect.getsource(run_demo.main)


def test_placeholder_publishable_key_fails_early(monkeypatch, capsys):
    """A pk_test_ placeholder passes the prefix check but Stripe.js refuses it: stop before starting anything."""
    monkeypatch.setattr(run_demo._dotenv, "load", lambda: [])
    monkeypatch.setattr(run_demo.subprocess, "Popen", lambda *a, **k: pytest.fail("started a node"))
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_offline")
    monkeypatch.setenv("STRIPE_PUBLISHABLE_KEY", "pk_test_placeholder")
    monkeypatch.setattr(run_demo.sys, "argv", ["run_demo.py"])
    assert run_demo.main() == 2
    err = capsys.readouterr().err
    assert "looks like a placeholder" in err and "pk_test_placeholder" not in err  # the key itself is never echoed
