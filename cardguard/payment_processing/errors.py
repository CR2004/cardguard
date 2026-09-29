"""Exceptions shared by the merchant node and every processor, so importing one never imports the other."""


class IssuerReject(Exception):
    """Verification or authorization refused; str(e) is the reason (never card data)."""
