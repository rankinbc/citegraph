"""Two small repos used by resolver, query, MCP and eval tests. Line numbers matter; edit with care."""

SHOP = {
    "src/shop/__init__.py": "",
    "src/shop/orders.py": (
        "import os\n"
        "from shop.payments import charge\n"
        "from billing.invoices import create_invoice\n"
        "\n"
        "\n"
        "class OrderService:\n"
        "    def place_order(self, order):\n"
        "        self.validate(order)\n"
        "        total = compute_total(order)\n"
        "        charge(total)\n"
        "        create_invoice(order)\n"
        "        self.notifier.notify_customer(order)\n"
        "        return total\n"
        "\n"
        "    def validate(self, order):\n"
        "        return bool(order)\n"
        "\n"
        "\n"
        "def compute_total(order):\n"
        '    rate = os.environ.get("TAX_RATE")\n'
        "    return sum(order) * float(rate or 1)\n"
    ),
    "src/shop/payments.py": (
        "import os\n"
        "\n"
        "\n"
        "def charge(amount):\n"
        '    url = os.getenv("PAYMENT_API_URL")\n'
        "    return _send(amount, url)\n"
        "\n"
        "\n"
        "def _send(amount, url):\n"
        "    return amount\n"
    ),
    "src/shop/cli.py": (
        "from shop.orders import OrderService\n"
        "\n"
        "\n"
        "def main():\n"
        "    OrderService().place_order([1, 2])\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    ),
    "appsettings.json": (
        "{\n"
        '  "Payment": {\n'
        '    "ApiUrl": "https://example.invalid",\n'
        '    "TimeoutSeconds": 30\n'
        "  },\n"
        '  "TAX_RATE": "0.2"\n'
        "}\n"
    ),
    ".env.example": "PAYMENT_API_URL=\nTAX_RATE=0.2\n",
    "pyproject.toml": '[project]\nname = "shop"\n\n[project.scripts]\nshop = "shop.cli:main"\n',
}

BILLING = {
    "src/billing/__init__.py": "",
    "src/billing/invoices.py": (
        "def create_invoice(order):\n"
        "    return render_invoice(order)\n"
        "\n"
        "\n"
        "def render_invoice(order):\n"
        "    return str(order)\n"
    ),
    "src/billing/notifications.py": "def notify_customer(order):\n    return order\n",
}

SAMPLE = {"shop": SHOP, "billing": BILLING}

PO = "shop.orders.OrderService.place_order"

SAMPLE_EDGES = {
    ("shop.orders", "shop.payments.charge", "import", "import_scope"),
    ("shop.orders", "billing.invoices.create_invoice", "import", "import_scope"),
    (PO, "shop.orders.OrderService.validate", "call", "same_file"),
    (PO, "shop.orders.compute_total", "call", "same_file"),
    (PO, "shop.payments.charge", "call", "import_scope"),
    (PO, "billing.invoices.create_invoice", "call", "import_scope"),
    (PO, "billing.notifications.notify_customer", "call", "cross_repo_unique"),
    ("shop.payments.charge", "shop.payments._send", "call", "same_file"),
    ("shop.cli", "shop.orders.OrderService", "import", "import_scope"),
    ("shop.cli.main", "shop.orders.OrderService", "instantiate", "import_scope"),
    ("shop.cli.main", PO, "call", "repo_unique"),
    ("shop.cli", "shop.cli.main", "call", "same_file"),
    ("billing.invoices.create_invoice", "billing.invoices.render_invoice", "call", "same_file"),
}
