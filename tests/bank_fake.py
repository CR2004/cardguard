"""The bank attestation node in-process for the offline tests: the real BankAttestor behind the same
two calls the merchant's BankClient makes, with refusals turned into "no answer" as over HTTP."""
from cardguard.bank.node import BankAttestor, Refused


class LocalBank:
    def __init__(self, attestor: BankAttestor | None = None, merchant_id: str = "cardguard-store"):
        self.node, self.merchant_id = attestor or BankAttestor(), merchant_id
        self.calls: list[tuple] = []

    def attest(self, card_ref, card_region):
        self.calls.append(("attest", card_ref, card_region))
        try:
            return self.node.attest(card_ref, card_region)
        except Refused:
            return None

    def travel_check(self, card_ref, card_region, buyer_region, decision_id):
        self.calls.append(("travel_check", card_ref, card_region, buyer_region, decision_id))
        try:
            return self.node.travel_check(card_ref, card_region, buyer_region, decision_id, merchant_id=self.merchant_id)
        except Refused:
            return None
