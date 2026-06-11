"""HTML report generation for validation results."""

import json
import math
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, Template


def _make_json_safe(obj: Any) -> Any:
    """Recursively replace NaN/Infinity with None for JSON serialization."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(v) for v in obj]
    return obj


def _safe_tojson(obj: Any) -> str:
    """JSON-serialize an object, handling NaN/Infinity values."""
    return json.dumps(_make_json_safe(obj))


def create_html_report(
    report_data: dict[str, Any],
    template_path: Path | None = None,
) -> str:
    """Generate an HTML report from validation results.

    Parameters
    ----------
    report_data
        Dictionary containing all data needed for the report, including:
        - summary: Overall pass/fail counts
        - comparisons: List of comparison details
        - plots: Dictionary mapping comparison keys to plot data
    template_path
        Optional path to custom Jinja2 template. If None, uses default template.

    Returns
    -------
        HTML string of the generated report
    """
    if template_path is None:
        template_path = _get_default_template_path()

    template = _load_template(template_path)
    html_content = template.render(**report_data)

    return html_content


def _load_template(template_path: Path) -> Template:
    """Load a Jinja2 template from the given path.

    Parameters
    ----------
    template_path
        Path to the Jinja2 template file

    Returns
    -------
        Loaded Jinja2 Template object
    """
    template_dir = template_path.parent
    template_name = template_path.name

    env = Environment(loader=FileSystemLoader(str(template_dir)))
    env.filters["safe_tojson"] = _safe_tojson
    template = env.get_template(template_name)

    return template


def _get_default_template_path() -> Path:
    """Get the path to the default report template.

    Returns
    -------
        Path to the default template file
    """
    # Template will be in a 'templates' directory within the automated_validation package
    current_file = Path(__file__)
    validation_package = current_file.parent.parent
    template_path = validation_package / "templates" / "report_template.html"

    return template_path


def save_html_report(html_content: str, output_path: Path) -> Path:
    """Save HTML content to a file.

    Parameters
    ----------
    html_content
        The HTML string to save
    output_path
        Path where the HTML file should be saved

    Returns
    -------
        Absolute path to the saved file
    """
    # Convert to absolute path
    output_path = output_path.resolve()

    # Create parent directories if they don't exist
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write the file
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return output_path
