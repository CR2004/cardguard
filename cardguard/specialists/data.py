"""Raw columns for the specialist experiment: IEEE-CIS transaction + identity files, or synthetic.

A "raw" dict is column-oriented numpy (memory: the transaction CSV is 650 MB, 394 columns; we keep
~40 of them). Numeric columns use NaN for missing; categorical columns are small int codes with -1 for
missing and a vocab list per column, so no strings are held per row.

Identity file (train_identity.csv) is joined by TransactionID. Only ~a quarter of transactions have
a row, so raw["has_identity"] is an explicit flag and the device flags are 0 where it is False.
The value formats parsed below (DeviceType mobile/desktop, id_31 "chrome 63.0", id_33 "1920x1080",
id_23 "IP_PROXY:ANONYMOUS", ...) are from community descriptions of the data; verify on the real file.
"""
from __future__ import annotations

import csv
import os
from array import array

import numpy as np

from cardguard import ROOT

TX_CSV = str(ROOT / "datasets" / "train_transaction.csv")
ID_CSV = str(ROOT / "datasets" / "train_identity.csv")
CACHE = str(ROOT / "datasets" / "specialist_features.npz")  # git-ignored with the rest of datasets/

C_COLS = [f"C{i}" for i in range(1, 15)]
M_COLS = [f"M{i}" for i in range(1, 10)]
D_EXTRA = ["D2", "D4", "D5", "D10", "D11", "D15"]  # day-gap columns beyond D1/D3 (history timing)
NUM_COLS = ["TransactionDT", "TransactionAmt", "addr1", "addr2", "dist1", "dist2", "D1", "D3",
            "card1", "card2", "card3", "card5", *C_COLS, *D_EXTRA]
CAT_COLS = ["ProductCD", "card6", "card4", "P_emaildomain", "R_emaildomain", *M_COLS]


def available() -> bool:
    return os.path.exists(TX_CSV)


# ---------- identity file ----------

def _has(text: str, *needles: str) -> float:
    t = text.lower()
    return float(any(n in t for n in needles))


def _screen_area(res: str) -> float:
    """'1920x1080' -> area scaled so 1920x1080 ~ 0.5; unparsable -> 0 (see screen_missing)."""
    try:
        w, h = res.lower().split("x")
        return min(int(w) * int(h) / 4_147_200, 1.0)  # 4K = 3840x2160 caps at ~2, clipped to 1
    except ValueError:
        return 0.0


# name -> function(row dict) -> float in [0, 1]. Order is the column order of raw["ident"].
_IDENT = {
    "mobile": lambda r: float(r.get("DeviceType", "") == "mobile"),
    "desktop": lambda r: float(r.get("DeviceType", "") == "desktop"),
    "dev_windows": lambda r: _has(r.get("DeviceInfo", ""), "windows"),
    "dev_ios": lambda r: _has(r.get("DeviceInfo", ""), "ios"),
    "dev_macos": lambda r: _has(r.get("DeviceInfo", ""), "macos"),
    "dev_samsung": lambda r: _has(r.get("DeviceInfo", ""), "sm-", "samsung"),
    "dev_huawei": lambda r: _has(r.get("DeviceInfo", ""), "huawei"),
    "dev_moto": lambda r: _has(r.get("DeviceInfo", ""), "moto"),
    "dev_lg": lambda r: _has(r.get("DeviceInfo", ""), "lg-", "lgm"),
    "dev_missing": lambda r: float(r.get("DeviceInfo", "") == ""),
    "os_windows": lambda r: _has(r.get("id_30", ""), "windows"),
    "os_ios": lambda r: _has(r.get("id_30", ""), "ios"),
    "os_android": lambda r: _has(r.get("id_30", ""), "android"),
    "os_mac": lambda r: _has(r.get("id_30", ""), "mac"),
    "os_missing": lambda r: float(r.get("id_30", "") == ""),
    "br_chrome": lambda r: _has(r.get("id_31", ""), "chrome"),
    "br_safari": lambda r: _has(r.get("id_31", ""), "safari"),
    "br_firefox": lambda r: _has(r.get("id_31", ""), "firefox"),
    "br_edge": lambda r: _has(r.get("id_31", ""), "edge"),
    "br_ie": lambda r: _has(r.get("id_31", ""), "ie "),
    "br_samsung": lambda r: _has(r.get("id_31", ""), "samsung"),
    "br_missing": lambda r: float(r.get("id_31", "") == ""),
    "screen_area": lambda r: _screen_area(r.get("id_33", "")),
    "screen_missing": lambda r: float(r.get("id_33", "") == ""),
    "proxy_anonymous": lambda r: _has(r.get("id_23", ""), "anonymous"),
    "proxy_hidden": lambda r: _has(r.get("id_23", ""), "hidden"),
    "proxy_transparent": lambda r: _has(r.get("id_23", ""), "transparent"),
    "id15_new": lambda r: float(r.get("id_15", "") == "New"),
    "id15_unknown": lambda r: float(r.get("id_15", "") == "Unknown"),
    "id28_new": lambda r: float(r.get("id_28", "") == "New"),
    "id29_notfound": lambda r: float(r.get("id_29", "") == "NotFound"),
}
IDENT_NAMES = list(_IDENT)


def identity_flags(row: dict) -> list[float]:
    """One identity-file row (column name -> string) -> device flags in IDENT_NAMES order."""
    return [fn(row) for fn in _IDENT.values()]


def read_identity(txids: np.ndarray, path: str = ID_CSV) -> tuple[np.ndarray, np.ndarray]:
    """(has_identity bool[n], ident float[n, len(IDENT_NAMES)]) aligned to txids."""
    pos = {t: i for i, t in enumerate(txids.tolist())}
    has = np.zeros(len(txids), dtype=bool)
    ident = np.zeros((len(txids), len(IDENT_NAMES)))
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        rd = csv.DictReader(fh)
        rd.fieldnames = [f.replace("-", "_") for f in rd.fieldnames]  # test file writes id-01
        for row in rd:
            i = pos.get(int(row["TransactionID"]))
            if i is not None:
                has[i] = True
                ident[i] = identity_flags(row)
    return has, ident


# ---------- transaction file ----------

def _num(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return np.nan


def read_transactions(path: str = TX_CSV, limit: int | None = None) -> dict:
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        r = csv.reader(fh)
        cols = next(r)
        ix = {c: i for i, c in enumerate(cols)}
        num_ix = [ix[c] for c in NUM_COLS]
        cat_ix = [ix[c] for c in CAT_COLS]
        num, cat = array("d"), array("h")
        txid, y = array("q"), array("b")
        vocab = [dict() for _ in CAT_COLS]
        for k, row in enumerate(r):
            if limit is not None and k >= limit:
                break
            num.extend([_num(row[i]) for i in num_ix])
            for j, i in enumerate(cat_ix):
                v = row[i]
                cat.append(-1 if v == "" else vocab[j].setdefault(v, len(vocab[j])))
            txid.append(int(row[ix["TransactionID"]]))
            y.append(int(row[ix["isFraud"]]))
    num = np.frombuffer(num, dtype=float).reshape(-1, len(NUM_COLS))
    cat = np.frombuffer(cat, dtype=np.int16).reshape(-1, len(CAT_COLS)).astype(np.int64)
    col = lambda name: num[:, NUM_COLS.index(name)]
    cc = lambda name: cat[:, CAT_COLS.index(name)]
    words = [sorted(v, key=v.get) for v in vocab]
    return {
        "txid": np.frombuffer(txid, dtype=np.int64), "y": np.frombuffer(y, dtype=np.int8).astype(float),
        "dt": col("TransactionDT"), "amt": col("TransactionAmt"),
        "addr1": col("addr1"), "addr2": col("addr2"), "dist1": col("dist1"), "dist2": col("dist2"),
        "D1": col("D1"), "D3": col("D3"),
        "card": num[:, [NUM_COLS.index(c) for c in ("card1", "card2", "card3", "card5")]],
        "C": num[:, [NUM_COLS.index(c) for c in C_COLS]],
        "D": num[:, [NUM_COLS.index(c) for c in D_EXTRA]],
        "prod": cc("ProductCD"), "card6": cc("card6"), "card4": cc("card4"), "pemail": cc("P_emaildomain"),
        "remail": cc("R_emaildomain"), "M": cat[:, [CAT_COLS.index(c) for c in M_COLS]],
        "vocab": {"prod": words[0], "card6": words[1], "card4": words[2], "pemail": words[3],
                  "remail": words[4], "M": words[5:]},
        "has_identity": np.zeros(len(txid), dtype=bool), "ident": np.zeros((len(txid), len(IDENT_NAMES))),
    }


def load_raw(limit: int | None = None) -> dict:
    """Transactions, plus identity when train_identity.csv is present."""
    raw = read_transactions(limit=limit)
    if os.path.exists(ID_CSV):
        raw["has_identity"], raw["ident"] = read_identity(raw["txid"])
    else:
        print(f"note: {ID_CSV} not found; the device specialist will have no data")
    return raw


# ---------- synthetic ----------

FRAUD_TYPES = ["transaction", "identity", "device", "geo", "behavior", "network"]


def synthetic_raw(n: int = 12000, seed: int = 0) -> dict:
    """Six fraud types, each visible mainly to ONE specialist, so no single family sees them all.

    transaction: round amounts and inflated C counts     identity: no payer email, new card, M flags F
    device: anonymous proxy / new-device flags (rows have identity)   geo: rare address, far distance
    behavior: amount and hour far from this card's own habit          network: many fresh cards on one
    address+email pair. The merchant family has no injected signal (a specialist that adds nothing).
    """
    rng = np.random.default_rng(seed)
    n_cards = n // 4
    cid = rng.integers(0, n_cards, n)
    kind = rng.choice(len(FRAUD_TYPES) + 1, n, p=[0.94] + [0.01] * len(FRAUD_TYPES))  # 0 = legit
    is_ = lambda name: kind == FRAUD_TYPES.index(name) + 1

    home_hour = rng.integers(8, 22, n_cards)
    card_la = rng.normal(3.5, 1.0, n_cards)
    card_age = rng.integers(5, 400, n_cards)
    hour = np.clip(np.rint(home_hour[cid] + rng.normal(0, 1.5, n)), 0, 23)
    hour = np.where(is_("behavior"), (home_hour[cid] + 12) % 24, hour)
    la = card_la[cid] + rng.normal(0, 0.3, n) + np.where(is_("behavior"), 1.5, 0.0)
    day = rng.integers(0, 60, n)
    dt = day * 86400.0 + hour * 3600 + rng.integers(0, 3600, n)
    amt = np.round(np.exp(la) - 1 + 1.37, 2)
    amt = np.where(is_("transaction"), np.maximum(np.round(amt / 50) * 50, 50), amt)

    card1 = np.where(is_("network"), n_cards + np.arange(n), cid).astype(float)
    card = np.c_[card1, 100 + cid % 900, np.full(n, 150.0), 200 + cid % 50]
    a1 = np.where(is_("network"), 100, 100 + cid % 50).astype(float)
    a1 = np.where(is_("geo"), 300 + rng.integers(0, 200, n), a1)
    a2 = np.where(is_("geo"), 60.0, np.where(rng.random(n) < 0.94, 87.0, np.nan))
    dist1 = np.where(is_("geo"), 3000 + rng.exponential(2000, n),
                     np.where(rng.random(n) < 0.7, np.nan, rng.exponential(20, n)))
    pe = np.where(is_("network"), 0, cid % 8)
    pe = np.where(is_("identity"), -1, pe)
    re_ = np.where(rng.random(n) < 0.6, -1, rng.integers(0, 12, n))
    d1 = np.where(is_("identity"), 0.0, card_age[cid] + day)
    d3 = np.where(rng.random(n) < 0.3, np.nan, rng.exponential(10, n))
    C = rng.poisson(1.0, (n, 14)).astype(float)
    C[is_("transaction"), :5] = rng.poisson(12, (int(is_("transaction").sum()), 5))
    M = rng.integers(-1, 2, (n, 9))
    M[is_("identity")] = 1  # vocab ["T", "F"]: F everywhere

    has = (rng.random(n) < 0.25) | is_("device")
    ident = (rng.random((n, len(IDENT_NAMES))) < 0.3).astype(float)
    for name in ("proxy_anonymous", "id29_notfound", "id15_new", "id28_new"):
        ident[is_("device"), IDENT_NAMES.index(name)] = 1.0
    ident[~has] = 0.0

    return {
        "txid": np.arange(n), "y": (kind > 0).astype(float), "dt": dt, "amt": amt,
        "addr1": a1, "addr2": a2, "dist1": dist1, "dist2": np.full(n, np.nan), "D1": d1, "D3": d3,
        "card": card, "C": C, "D": np.where(rng.random((n, len(D_EXTRA))) < 0.4, np.nan,
                                            rng.exponential(15, (n, len(D_EXTRA)))),
        "prod": rng.integers(0, 5, n), "card6": (rng.random(n) < 0.25).astype(int),
        "card4": rng.integers(0, 3, n),
        "pemail": pe.astype(int), "remail": re_.astype(int), "M": M,
        "vocab": {"prod": ["W", "C", "R", "H", "S"], "card6": ["debit", "credit"],
                  "card4": ["visa", "mastercard", "discover"],
                  "pemail": [f"mail{i}.com" for i in range(8)], "remail": [f"mail{i}.com" for i in range(12)],
                  "M": [["T", "F"] for _ in M_COLS]},
        "has_identity": has, "ident": ident,
    }
