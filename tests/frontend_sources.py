"""Source contracts for the modular frontend; serving is tested separately."""
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "src"


def frontend_sources() -> tuple[str, str, str]:
    markup = "\n".join(path.read_text() for path in sorted(FRONTEND.rglob("*.tsx")))
    script = "\n".join(path.read_text() for path in sorted(FRONTEND.rglob("*")) if path.suffix in {".js", ".ts", ".tsx"})
    css = "\n".join(path.read_text() for path in sorted((FRONTEND / "styles").glob("*.css")))
    return markup, script, css
