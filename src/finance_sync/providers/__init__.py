"""Bank-data providers. Each returns a FetchResult; nothing here can move money.

Only SimpleFIN is implemented. A Plaid provider can be added by implementing
the same `fetch(start, end, include_pending) -> FetchResult` method.
"""
