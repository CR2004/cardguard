"""Exceptions shared by the merchant node and the processor adapter."""


class ProcessorReject(Exception):
    """Verification or authorization refused by the processor; str(e) is the reason (never card data)."""
