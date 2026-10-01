import textwrap

from citegraph.extract.csharp import CSharpExtractor
from citegraph.models import ExtractResult

SRC = textwrap.dedent(
    """\
    global using System.Linq;
    using System;
    using Svc = Shop.Services.OrderService;
    using static System.Math;
    namespace Shop.Api;

    public partial class OrdersController : ControllerBase, IFoo<int>
    {
        private readonly IOrderService _orders;
        private IRepository<Order>? Repo { get; set; }

        public OrdersController(IOrderService orders) { _orders = orders; }
        static OrdersController() { }

        public async Task<int> Place(Order order, int n = 1)
        {
            var created = new Payment(1);
            Payment typed = new();
            using var conn = new Conn();
            await _orders.PlaceOrderAsync<int>(order);
            this.Validate(order);
            base.Dispose();
            Helper.Run(created);
            Foo();
            if (order is Order matched) matched.Ship();
            Lookup(out Customer found);
            found.Greet();
            Func<int> f = () => Compute(n);
            int Local(int q) => Compute(q);
            Repo.Save();
            return nameof(Place).Length;
        }

        public void Place(int id) { }
        void Validate(Order order) { }

        public class Nested { void Inner() { } }
    }

    public interface IOrderService { Task<int> PlaceOrderAsync(Order o); }
    public record Order(int Id);
    public record struct Point(int X);
    public struct Size { }
    public enum Color { Red }
    """
)
PATH = "src/Shop.Api/OrdersController.cs"
C = "Shop.Api.OrdersController"


def extract(src: str | bytes, path: str = PATH) -> ExtractResult:
    data = src.encode() if isinstance(src, str) else src
    return CSharpExtractor().extract(path, data)


def refs(result: ExtractResult, kind: str = "call") -> list[tuple[str, str, str | None, int]]:
    return [
        (r.from_qualified, r.to_name, r.receiver_type, r.line) for r in result.references if r.kind == kind
    ]


def test_symbols() -> None:
    result = extract(SRC)
    got = {(s.kind, s.qualified_name, s.line_start, s.param_count) for s in result.symbols}
    assert got == {
        ("module", "Shop.Api", 1, None),
        ("class", C, 7, None),
        ("method", f"{C}..ctor", 12, 1),
        ("method", f"{C}..cctor", 13, 0),
        ("method", f"{C}.Place", 15, 2),
        ("method", f"{C}.Place", 34, 1),  # overloads share one qualified name
        ("method", f"{C}.Validate", 35, 1),
        ("class", f"{C}.Nested", 37, None),
        ("method", f"{C}.Nested.Inner", 37, 0),
        ("interface", "Shop.Api.IOrderService", 40, None),
        ("method", "Shop.Api.IOrderService.PlaceOrderAsync", 40, 1),
        ("class", "Shop.Api.Order", 41, None),
        ("class", "Shop.Api.Point", 42, None),
        ("class", "Shop.Api.Size", 43, None),
        ("class", "Shop.Api.Color", 44, None),
    }
    assert result.module == "Shop.Api"
    assert not result.parse_error


def test_visibility() -> None:
    vis = {(s.qualified_name, s.line_start): s.visibility for s in extract(SRC).symbols}
    assert vis[(C, 7)] == "public"
    assert vis[(f"{C}.Validate", 35)] == "private"  # no modifier on a class member
    assert vis[(f"{C}.Nested.Inner", 37)] == "private"
    assert vis[("Shop.Api.IOrderService.PlaceOrderAsync", 40)] == "public"  # interface members default public
    internal = extract("class Hidden { }\n").symbols[1]
    assert internal.visibility == "public"  # a top-level type defaults to internal


def test_calls_use_declared_receiver_types() -> None:
    P = f"{C}.Place"
    assert refs(extract(SRC)) == [
        (P, "PlaceOrderAsync", "IOrderService", 20),  # field, generic arguments dropped
        (P, "this.Validate", None, 21),
        (P, "base.Dispose", None, 22),
        (P, "Helper.Run", None, 23),
        (P, "Foo", None, 24),
        (P, "Ship", "Order", 25),  # `is T x` pattern variable
        (P, "Lookup", None, 26),
        (P, "Greet", "Customer", 27),  # `out T x`
        (P, "Compute", None, 28),  # lambda body belongs to the method
        (P, "Compute", None, 29),  # local function body belongs to the method
        (P, "Save", "IRepository", 30),  # property, nullable and generic markers dropped
    ]


def test_declared_type_sources() -> None:
    src = textwrap.dedent(
        """\
        class K(IRepo repo)
        {
            void M(Order order)
            {
                Customer c = Find();
                var created = new Invoice();
                using var conn = new Conn();
                repo.Load();
                order.Ship();
                c.Greet();
                created.Send();
                conn.Open();
            }
        }
        """
    )
    receivers = {(to, receiver) for _, to, receiver, _ in refs(extract(src, "K.cs"))}
    assert {
        ("Load", "IRepo"),
        ("Ship", "Order"),
        ("Greet", "Customer"),
        ("Send", "Invoice"),
        ("Open", "Conn"),
    } <= receivers


def test_inner_scope_shadows_outer_declaration() -> None:
    src = textwrap.dedent(
        """\
        class K
        {
            private Outer _x;
            void M(Inner _x) { _x.Go(); }
            void N() { _x.Go(); }
        }
        """
    )
    assert [(to, receiver) for _, to, receiver, _ in refs(extract(src, "K.cs"))] == [
        ("Go", "Inner"),
        ("Go", "Outer"),
    ]


def test_instantiations_and_inheritance() -> None:
    result = extract(SRC)
    assert refs(result, "instantiate") == [
        (f"{C}.Place", "Payment", None, 17),
        (f"{C}.Place", "Payment", None, 18),  # `T x = new()` uses the declared type
        (f"{C}.Place", "Conn", None, 19),
    ]
    assert refs(result, "inherit") == [(C, "ControllerBase", None, 7), (C, "IFoo", None, 7)]


def test_record_base_and_global_alias() -> None:
    result = extract("record B(int X) : A(X), IZ;\nclass C : global::Sys.Base { }\n", "B.cs")
    assert [(r.from_qualified, r.to_name) for r in result.references] == [
        ("B", "A"),
        ("B", "IZ"),
        ("C", "Sys.Base"),
    ]


def test_using_directives() -> None:
    src = "global using System.Linq;\nglobal using static X.Y;\nglobal using G = A.B;\n"
    imports = [(i.local_name, i.target, i.line) for i in extract(SRC).imports + extract(src).imports]
    assert imports == [
        ("*global", "System.Linq", 1),
        ("*", "System", 2),
        ("Svc", "Shop.Services.OrderService", 3),
        ("*static", "System.Math", 4),
        ("*global", "System.Linq", 1),
        ("*global-static", "X.Y", 2),
        ("*global=G", "A.B", 3),
    ]


def test_block_and_nested_namespaces() -> None:
    src = textwrap.dedent(
        """\
        namespace A.B
        {
            using C.D;
            namespace Inner
            {
                class K { void M() { } }
            }
            class L { }
        }
        """
    )
    result = extract(src, "K.cs")
    assert [s.qualified_name for s in result.symbols] == ["A.B", "A.B.Inner.K", "A.B.Inner.K.M", "A.B.L"]
    assert result.module == "A.B"
    assert [(i.local_name, i.target) for i in result.imports] == [("*", "C.D")]


def test_partial_class_declarations_each_produce_a_symbol() -> None:
    src = "namespace N;\npartial class P { void A() { } }\npartial class P { void B() { } }\n"
    assert [s.qualified_name for s in extract(src, "P.cs").symbols] == ["N", "N.P", "N.P.A", "N.P", "N.P.B"]


def test_member_initializers_and_accessors_belong_to_the_type() -> None:
    src = textwrap.dedent(
        """\
        class K
        {
            private static readonly Thing _t = Make();
            int P => Compute();
            int Q { get { return Other(); } }
        }
        """
    )
    assert [(f, to) for f, to, _, _ in refs(extract(src, "K.cs"))] == [
        ("K", "Make"),
        ("K", "Compute"),
        ("K", "Other"),
    ]


def test_conditional_access_and_generic_calls() -> None:
    src = 'class K { Svc _s; void M() { _s?.Go(); _o?.Run(); Gen<int>(); int.TryParse("1", out var v); } }\n'
    assert [(to, receiver) for _, to, receiver, _ in refs(extract(src, "K.cs"))] == [
        ("Go", "Svc"),
        ("_o.Run", None),
        ("Gen", None),
        ("int.TryParse", None),
    ]


def test_config_reads() -> None:
    src = textwrap.dedent(
        """\
        class K
        {
            private readonly IConfiguration _config;
            void M(IConfiguration configuration, WebApplicationBuilder builder)
            {
                var a = _config["Payment:ApiUrl"];
                var b = configuration["Feature:On"];
                var c = builder.Configuration["Logging:Level"];
                var d = _config.GetSection("Payment").GetValue<int>("Timeout");
                var e = _config.GetConnectionString("Orders");
                var f = _config.GetRequiredSection("Auth");
                var g = Environment.GetEnvironmentVariable("TAX_RATE");
                var h = _items["NotConfig"];
                var i = _config[name];
            }
        }
        """
    )
    keys = [(k.key_path, k.line, k.origin, k.reader_qualified) for k in extract(src, "K.cs").config_keys]
    assert keys == [
        ("Payment:ApiUrl", 6, "code-read", "K.M"),
        ("Feature:On", 7, "code-read", "K.M"),
        ("Logging:Level", 8, "code-read", "K.M"),
        ("Payment:Timeout", 9, "code-read", "K.M"),  # a section read composes with its receiver
        ("Payment", 9, "code-read", "K.M"),
        ("ConnectionStrings:Orders", 10, "code-read", "K.M"),
        ("Auth", 11, "code-read", "K.M"),
        ("TAX_RATE", 12, "code-read", "K.M"),
    ]


def test_entry_points() -> None:
    main = extract("namespace N;\nclass P { static void Main(string[] args) { } }\n", "P.cs")
    assert [(e.kind, e.name, e.line) for e in main.entry_points] == [("program-main", "N.P.Main", 2)]
    top = extract("using System;\nvar app = Builder.Create(args);\napp.Run();\n", "src/Api/Program.cs")
    assert [(e.kind, e.name, e.line) for e in top.entry_points] == [("program-main", "Program", 2)]
    assert top.module == ""
    assert [(s.kind, s.qualified_name) for s in top.symbols] == [("module", "Program")]
    assert [(f, to) for f, to, _, _ in refs(top)] == [("Program", "Builder.Create"), ("Program", "app.Run")]


def test_malformed_source_sets_parse_error() -> None:
    result = extract("class Broken { void M( { Foo(); }\n", "Broken.cs")
    assert result.parse_error is True


def test_bom_and_crlf_keep_line_numbers() -> None:
    src = (
        "namespace A;\r\n\r\npublic class K\r\n{\r\n    void M()\r\n    {\r\n        Foo();\r\n    }\r\n}\r\n"
    )
    for data in (src.encode(), b"\xef\xbb\xbf" + src.encode()):
        result = extract(data, "K.cs")
        assert [(s.qualified_name, s.line_start) for s in result.symbols] == [
            ("A", 1),
            ("A.K", 3),
            ("A.K.M", 5),
        ]
        assert refs(result) == [("A.K.M", "Foo", None, 7)]
        assert not result.parse_error
