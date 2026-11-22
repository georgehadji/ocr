from omniocr.application.router import ScriptRouter
from omniocr.domain.models import BBox, Confidence, OCRLine, Script, TenantContext


class NamedEngine:
    def __init__(self, name: str) -> None:
        self.name = name


def test_script_router_uses_script_rule_and_default() -> None:
    ancient = NamedEngine("kraken")
    fallback = NamedEngine("tesseract")
    router = ScriptRouter({Script.ANCIENT: (ancient,)}, default=(fallback,))
    context = TenantContext("org", "user", "desktop")
    ancient_line = OCRLine("a", "", Confidence(0), BBox(0, 0, 1, 1), Script.ANCIENT)
    unknown_line = OCRLine("u", "", Confidence(0), BBox(0, 0, 1, 1), Script.UNKNOWN)

    assert router.route(ancient_line, context) == (ancient,)
    assert router.route(unknown_line, context) == (fallback,)


def test_engines_lists_each_engine_once_in_routing_order() -> None:
    kraken = NamedEngine("kraken")
    tesseract = NamedEngine("tesseract")
    vlm = NamedEngine("vlm")
    router = ScriptRouter(
        {Script.ANCIENT: (kraken, tesseract), Script.POLYTONIC: (kraken, tesseract, vlm)},
        default=(tesseract,),
    )

    assert router.engines() == (kraken, tesseract, vlm)


def test_engines_includes_a_default_no_script_routes_to() -> None:
    only_default = NamedEngine("tesseract")

    assert ScriptRouter({}, default=(only_default,)).engines() == (only_default,)
