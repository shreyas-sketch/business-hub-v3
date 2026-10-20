"""Payments coming in from an owner's own payment gateway (v2.1, 3.5): one connector per gateway, one shape for the hub.

    razorpay.py   Razorpay webhooks (payment.captured, refund.processed)
Later: PayU, Cashfree, Instamojo — each a module with the same two functions as base.Connector.
"""
from . import razorpay

CONNECTORS = {"razorpay": razorpay.CONNECTOR}
