"""
Prompt loader — reads versioned prompt files from the prompts/ directory.

Prompts are stored as Markdown files (prompts/extract_invoice_v1.md) rather
than hardcoded strings. This means:
  - Prompt versions are tracked in git
  - The prompt version is stored per extraction in the DB
  - Non-engineers can iterate on prompts without touching Python code
  - The eval harness can compare two prompt versions on the same test set

The loader caches file contents in memory (prompts don't change during a run).
Template variables are filled in with str.format_map() — simple and explicit,
no Jinja2 dependency needed.
"""

from functools import lru_cache
from pathlib import Path
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# Prompts directory is two levels up from this file (repo root / prompts/)
PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "prompts"

# Fallback if the prompts dir isn't mounted (e.g. running tests locally)
if not PROMPTS_DIR.exists():
    PROMPTS_DIR = Path("/app/prompts")


@lru_cache(maxsize=32)
def load_prompt(prompt_name: str) -> str:
    """
    Load a prompt file by name (without .md extension).
    Cached after first read — prompts are immutable during a pipeline run.

    Raises FileNotFoundError if the prompt doesn't exist.
    """
    path = PROMPTS_DIR / f"{prompt_name}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {path}\n"
            f"Looked in: {PROMPTS_DIR}\n"
            f"Available prompts: {list_prompts()}"
        )
    content = path.read_text(encoding="utf-8")
    logger.debug("prompt_loaded", name=prompt_name, path=str(path))
    return content


def render_prompt(prompt_name: str, **kwargs) -> str:
    """
    Load a prompt and fill in template variables with {key} syntax.

    Example:
        render_prompt("classify_v1", document_text="BILL OF LADING...")
    """
    template = load_prompt(prompt_name)
    try:
        return template.format_map(kwargs)
    except KeyError as e:
        raise ValueError(
            f"Prompt '{prompt_name}' requires variable {e} but it wasn't provided. "
            f"If this is a literal brace in the prompt (e.g. a JSON example), "
            f"escape it as {{{{ }}}} instead of {{ }}."
        )


def list_prompts() -> list[str]:
    """List all available prompt names (without .md extension)."""
    if not PROMPTS_DIR.exists():
        return []
    return [p.stem for p in sorted(PROMPTS_DIR.glob("*.md"))]


def get_prompt_version(prompt_name: str) -> str:
    """
    Return the prompt version string (the filename without .md).
    This is stored in the extractions table so we can reproduce any run.
    """
    return prompt_name
