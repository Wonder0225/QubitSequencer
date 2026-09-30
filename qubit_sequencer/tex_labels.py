r"""Small, dependency-free TeX subset for device labels in Qt text items.

Supported: grouped/single-character subscripts and superscripts, common symbol
commands, and \mathrm{...}/\text{...}. Unsupported commands stay visible.
All literal text is HTML-escaped before it reaches Qt's rich-text renderer.
"""

from __future__ import annotations

import html

SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "lambda": "λ", "mu": "μ",
    "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ",
    "tau": "τ", "phi": "φ", "varphi": "ϕ", "chi": "χ", "psi": "ψ",
    "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ",
    "Omega": "Ω", "pm": "±", "times": "×", "cdot": "·",
    "rightarrow": "→", "leftrightarrow": "↔", "leq": "≤", "geq": "≥", "neq": "≠",
}


def tex_to_html(source: str) -> str:
    """Return Qt-compatible escaped rich text; malformed groups render literally."""
    index = 0

    def command() -> str:
        nonlocal index
        index += 1
        if index >= len(source):
            return "\\"
        start = index
        if source[index].isalpha():
            while index < len(source) and source[index].isalpha():
                index += 1
        else:
            index += 1
        name = source[start:index]
        if name in ("mathrm", "text") and index < len(source) and source[index] == "{":
            index += 1
            return group(True)
        if name in SYMBOLS:
            return html.escape(SYMBOLS[name])
        if len(name) == 1 and not name.isalpha():
            return html.escape(name)
        return html.escape("\\" + name)

    def atom() -> str:
        nonlocal index
        if index >= len(source):
            return ""
        if source[index] == "{":
            index += 1
            return group(True)
        if source[index] == "\\":
            return command()
        char = source[index]
        index += 1
        return html.escape(char)

    def group(nested: bool = False) -> str:
        nonlocal index
        pieces = []
        while index < len(source):
            char = source[index]
            if char == "}":
                if not nested:
                    raise ValueError("unexpected closing brace")
                index += 1
                return "".join(pieces)
            if char in "_^":
                index += 1
                tag = "sub" if char == "_" else "sup"
                pieces.append(f"<{tag}>{atom()}</{tag}>")
            elif char == "{":
                index += 1
                pieces.append(group(True))
            elif char == "\\":
                pieces.append(command())
            else:
                pieces.append(html.escape(char))
                index += 1
        if nested:
            raise ValueError("unclosed group")
        return "".join(pieces)

    try:
        return group()
    except ValueError:
        return html.escape(source)
