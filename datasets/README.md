# datasets/

Put `train_transaction.csv` from the IEEE-CIS Fraud Detection competition here:
https://www.kaggle.com/competitions/ieee-fraud-detection/data (sign in, accept the rules,
download only that file, about 650 MB). `python -m cardguard.data.ieee_cis` builds `features.npz`
next to it. Licensed under the Kaggle competition rules: research and hackathon use, not commercial.
The specialist models (`python -m cardguard.specialists.experiment`) read the same `train_transaction.csv`
and write `specialist_features.npz` here.
Nothing in this folder is committed except this file.
