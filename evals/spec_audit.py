"""Catalog-derived spec-mention audit for rule_7 (no-fab) labeling hints.

Parses the Specs column of the workspace catalog into canonical tokens per
product, then scans each seller turn for substring matches of those tokens.
Matching is whitespace-insensitive and case-insensitive but otherwise uses
only the literal text the user wrote in the catalog — no regex, no
assumption about what a spec "looks like" in any particular domain.

Per the project's eval-infra-domain-agnostic rule: the only domain input is
the catalog itself. Works for any catalog whose table has a Specs column.
"""

from __future__ import annotations


def parse_catalog_specs(catalog_text: str) -> dict[str, list[str]]:
    """Return {product_name: [spec_token, ...]} from the catalog's Specs column.

    Each comma-separated piece in the cell becomes one token, trimmed but
    otherwise unchanged (preserving casing and punctuation like '6.1"',
    'S Pen', '50MP Hasselblad'). The product is read from the column whose
    header is 'Product' (case-insensitive); the spec column is the one whose
    header is 'Specs'.
    """
    product_idx: int | None = None
    spec_idx: int | None = None
    specs: dict[str, list[str]] = {}

    for line in catalog_text.splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if product_idx is None:
            headers = [c.lower() for c in cells]
            if "specs" not in headers:
                continue
            spec_idx = headers.index("specs")
            product_idx = headers.index("product") if "product" in headers else 0
            continue
        if spec_idx is None or len(cells) <= spec_idx:
            continue
        product = cells[product_idx]
        tokens = [t.strip() for t in cells[spec_idx].split(",") if t.strip()]
        if product and tokens:
            specs[product] = tokens
    return specs


def audit_conversation(
    transcript: list[dict],
    catalog_specs: dict[str, list[str]],
) -> list[dict]:
    """Per seller turn, return whichever catalog tokens appear in the message.

    Each mention also includes a wrong_product flag: True when the seller
    explicitly named one or more catalog products in this turn AND none of
    them owns the matched token. That signals the "wrong-product attribution"
    failure mode (rule_7 principle 5).

    Output: list of {turn_number, message, content, products_in_turn,
    mentions} where each mention is {token, products, span, wrong_product}.
    """
    token_to_products: dict[str, list[str]] = {}
    for product, tokens in catalog_specs.items():
        for tok in tokens:
            token_to_products.setdefault(tok, []).append(product)
    sorted_tokens = sorted(token_to_products.keys(), key=len, reverse=True)
    sorted_products = sorted(catalog_specs.keys(), key=len, reverse=True)

    audits = []
    for turn in transcript:
        if turn.get("role") != "seller":
            continue
        content = turn.get("content", "") or ""
        mentions = _find_token_mentions(content, sorted_tokens, token_to_products)
        if not mentions:
            continue
        products_in_turn = _products_in_turn(content, sorted_products)
        if products_in_turn:
            for m in mentions:
                m["wrong_product"] = not any(p in m["products"] for p in products_in_turn)
        else:
            for m in mentions:
                m["wrong_product"] = False
        audits.append({
            "turn_number": turn.get("turn_number"),
            "message": turn.get("message"),
            "content": content,
            "products_in_turn": sorted(products_in_turn),
            "mentions": mentions,
        })
    return audits


def _products_in_turn(content: str, sorted_products: list[str]) -> set[str]:
    """Return the set of catalog product names explicitly named in the turn.

    Longest-match-first to avoid double-counting (e.g. 'Samsung Galaxy S24
    Ultra' wins over 'Samsung Galaxy S24' when both could match the same
    span). Substrings are not subsumed: a turn that names both 'iPhone 15'
    and 'iPhone 15 Pro Max' in separate positions yields both.
    """
    found: set[str] = set()
    claimed = [False] * len(content)
    lower = content.lower()
    for p in sorted_products:
        p_lower = p.lower()
        if not p_lower:
            continue
        start = 0
        while True:
            i = lower.find(p_lower, start)
            if i == -1:
                break
            j = i + len(p_lower)
            if not any(claimed[i:j]):
                found.add(p)
                for k in range(i, j):
                    claimed[k] = True
            start = i + 1
    return found


def _find_token_mentions(
    content: str,
    sorted_tokens: list[str],
    token_to_products: dict[str, list[str]],
) -> list[dict]:
    """Locate non-overlapping case- and whitespace-insensitive matches.

    Whitespace insensitivity: '128 GB' in a seller turn matches the catalog
    token '128GB'. That's the only normalization applied; everything else is
    literal text from the catalog.
    """
    stripped, idx_map = _strip_to_original_map(content)
    spans: list[tuple[int, int, str]] = []
    for tok in sorted_tokens:
        tok_stripped = "".join(c.lower() for c in tok if not c.isspace())
        if not tok_stripped:
            continue
        start = 0
        while True:
            i = stripped.find(tok_stripped, start)
            if i == -1:
                break
            j = i + len(tok_stripped)
            orig_start = idx_map[i]
            orig_end = idx_map[j - 1] + 1
            if not any(s < orig_end and orig_start < e for s, e, _ in spans):
                spans.append((orig_start, orig_end, tok))
            start = i + 1
    spans.sort()
    return [
        {"token": tok, "products": token_to_products[tok], "span": [s, e]}
        for s, e, tok in spans
    ]


def _strip_to_original_map(content: str) -> tuple[str, list[int]]:
    """Build (whitespace-stripped lowercase, idx_map) where idx_map[i] is the
    original-content index of the i-th non-whitespace character."""
    chars: list[str] = []
    idx_map: list[int] = []
    for i, ch in enumerate(content):
        if not ch.isspace():
            chars.append(ch.lower())
            idx_map.append(i)
    return "".join(chars), idx_map
