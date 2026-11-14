"""Read-only result hints; these never grant permission to approve a payment."""


def result_hint(order):
    stage = {
        "created": "prepared",
        "confirming": "approval_processing",
        "paid": "paid",
        "refunding": "cancellation_pending",
        "refunded": "refunded",
        "abandoned": "authorization_closed",
    }.get(order["status"], "unavailable")
    if order["status"] == "pending":
        stage = "approval_pending" if order["payment_key"] else "authentication_open"
    return {
        "schemaVersion": 1,
        "orderId": order["id"],
        "orderVersion": order["version"],
        "stage": stage,
    }
