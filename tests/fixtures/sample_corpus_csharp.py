"""Two small C# repos: an interface-typed DI field, a cross-repo call through a using directive, a record,
a static helper, configuration reads and top-level statements. Line numbers matter; edit with care."""

ORDERING = {
    "src/Ordering.Domain/IOrderService.cs": (
        "namespace Ordering.Domain;\n"
        "\n"
        "public interface IOrderService\n"
        "{\n"
        "    Task<int> PlaceOrderAsync(Order order);\n"
        "}\n"
    ),
    "src/Ordering.Domain/OrderService.cs": (
        "using Payments;\n"
        "\n"
        "namespace Ordering.Domain;\n"
        "\n"
        "public class OrderService : IOrderService\n"
        "{\n"
        "    private readonly PaymentClient _payments;\n"
        "\n"
        "    public OrderService(PaymentClient payments)\n"
        "    {\n"
        "        _payments = payments;\n"
        "    }\n"
        "\n"
        "    public async Task<int> PlaceOrderAsync(Order order)\n"
        "    {\n"
        "        Validate(order);\n"
        "        var total = PriceCalculator.Total(order);\n"
        "        await _payments.ChargeAsync(total);\n"
        "        return total;\n"
        "    }\n"
        "\n"
        "    private void Validate(Order order)\n"
        "    {\n"
        "        order.EnsureItems();\n"
        "    }\n"
        "}\n"
    ),
    "src/Ordering.Domain/Order.cs": (
        "namespace Ordering.Domain;\n"
        "\n"
        "public record Order(int Id)\n"
        "{\n"
        "    public void EnsureItems()\n"
        "    {\n"
        "    }\n"
        "}\n"
        "\n"
        "public static class PriceCalculator\n"
        "{\n"
        "    public static int Total(Order order) => order.Id * 2;\n"
        "}\n"
    ),
    "src/Ordering.Api/OrdersController.cs": (
        "using Microsoft.Extensions.Configuration;\n"
        "using Ordering.Domain;\n"
        "\n"
        "namespace Ordering.Api\n"
        "{\n"
        "    public class OrdersController\n"
        "    {\n"
        "        private readonly IOrderService _orders;\n"
        "        private readonly IConfiguration _config;\n"
        "\n"
        "        public OrdersController(IOrderService orders, IConfiguration config)\n"
        "        {\n"
        "            _orders = orders;\n"
        "            _config = config;\n"
        "        }\n"
        "\n"
        "        public async Task<int> Post(int id)\n"
        "        {\n"
        '            var url = _config["Payment:ApiUrl"];\n'
        "            var order = new Order(id);\n"
        "            return await _orders.PlaceOrderAsync(order);\n"
        "        }\n"
        "    }\n"
        "}\n"
    ),
    "src/Ordering.Api/Program.cs": (
        "using Ordering.Api;\n"
        "\n"
        "var app = WebApp.Create(args);\n"
        'var timeout = app.Configuration.GetValue<int>("Payment:TimeoutSeconds");\n'
        "app.Run();\n"
    ),
    "src/Ordering.Api/appsettings.json": (
        '{\n  "Payment": {\n    "ApiUrl": "https://example.invalid",\n    "TimeoutSeconds": 30\n  }\n}\n'
    ),
}

PAYMENTS = {
    "src/Payments/PaymentClient.cs": (
        "namespace Payments;\n"
        "\n"
        "public class PaymentClient : IPaymentClient\n"
        "{\n"
        "    public async Task ChargeAsync(int amount)\n"
        "    {\n"
        '        var key = Environment.GetEnvironmentVariable("PAYMENT_API_KEY");\n'
        "        await SendAsync(amount);\n"
        "    }\n"
        "\n"
        "    private Task SendAsync(int amount)\n"
        "    {\n"
        "        return Task.CompletedTask;\n"
        "    }\n"
        "}\n"
        "\n"
        "public interface IPaymentClient\n"
        "{\n"
        "    Task ChargeAsync(int amount);\n"
        "}\n"
    ),
}

CSHARP_SAMPLE = {"ordering": ORDERING, "payments": PAYMENTS}

PLACE = "Ordering.Domain.OrderService.PlaceOrderAsync"
POST = "Ordering.Api.OrdersController.Post"

CSHARP_SAMPLE_EDGES = {
    ("Ordering.Domain.OrderService", "Ordering.Domain.IOrderService", "inherit", "same_namespace"),
    (PLACE, "Ordering.Domain.OrderService.Validate", "call", "same_file"),
    (PLACE, "Ordering.Domain.PriceCalculator.Total", "call", "same_namespace"),
    (PLACE, "Payments.PaymentClient.ChargeAsync", "call", "declared_type"),
    ("Ordering.Domain.OrderService.Validate", "Ordering.Domain.Order.EnsureItems", "call", "declared_type"),
    (POST, "Ordering.Domain.Order", "instantiate", "import_scope"),
    (POST, "Ordering.Domain.IOrderService.PlaceOrderAsync", "call", "declared_type"),
    ("Payments.PaymentClient", "Payments.IPaymentClient", "inherit", "same_file"),
    ("Payments.PaymentClient.ChargeAsync", "Payments.PaymentClient.SendAsync", "call", "same_file"),
}
