# datasets/

Put `train_transaction.csv` from the IEEE-CIS Fraud Detection competition here:
https://www.kaggle.com/competitions/ieee-fraud-detection/data (sign in, accept the rules,
download only that file, about 650 MB). `python -m cardguard.data.ieee_cis` builds `features.npz`
next to it. Licensed under the Kaggle competition rules: research and hackathon use, not commercial.
The specialist experiment (`python -m cardguard.specialists.experiment`) also reads
`train_identity.csv` (same page, about 27 MB, optional: without it the device specialist has no data)
and writes `specialist_features.npz`. The `test_*.csv` files have no labels and are not needed.
Nothing in this folder is committed except this file.
