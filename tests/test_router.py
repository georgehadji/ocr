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
