"""Executor agents, one per intent.

Rule of thumb used throughout: numbers, parsing and validation are done in
plain Python (deterministic, testable, no hallucinated totals); the LLM is only
used to turn already-computed facts into readable text.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .llm import LLMProvider
from .models import EmailTask, TaskResult


class ExecutionError(Exception):
    """Bad or missing input. Retrying will not help, so the task fails immediately."""


@dataclass
class Context:
    task: EmailTask
    files: list[Path]          # this email's attachments + files remembered for the thread
    memory: str
    llm: LLMProvider


def _find(files: list[Path], *, suffix: str, contains: str = "") -> Path:
    for f in files:
        if f.suffix.lower() == suffix and contains in f.name.lower():
            return f
    raise ExecutionError(f"no {contains or ''}{suffix} attachment found".replace("  ", " "))


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _money(x: float) -> str:
    return f"${x:,.2f}"


# ---------------------------------------------------------------------------

def sales_summary(ctx: Context) -> TaskResult:
    rows = _read_csv(_find(ctx.files, suffix=".csv", contains="sales"))
    by_region: dict[str, float] = defaultdict(float)
    by_product: dict[str, float] = defaultdict(float)
    units = 0
    for r in rows:
        revenue = float(r["revenue"])
        by_region[r["region"]] += revenue
        by_product[r["product"]] += revenue
        units += int(r["units"])
    total = sum(by_region.values())

    lines = [f"Total revenue: {_money(total)} from {units} units across {len(rows)} orders."]
    lines.append("By region: " + ", ".join(
        f"{k} {_money(v)}" for k, v in sorted(by_region.items(), key=lambda kv: -kv[1])))
    wants_products = "product" in ctx.task.text.lower()
    if wants_products:
        lines.append("By product: " + ", ".join(
            f"{k} {_money(v)}" for k, v in sorted(by_product.items(), key=lambda kv: -kv[1])))
    top_region = max(by_region, key=by_region.get)
    lines.append(f"Top region: {top_region}.")
    facts = "\n".join(f"- {line}" for line in lines)

    narrative = ctx.llm.complete(
        "Write a short, plain-English sales update for a manager using only these figures.\n"
        f"FACTS:\n{facts}",
        purpose="write",
    )
    table = {"total_revenue": round(total, 2), "units": units,
             "by_region": {k: round(v, 2) for k, v in by_region.items()}}
    if wants_products:
        table["by_product"] = {k: round(v, 2) for k, v in by_product.items()}
    return TaskResult("completed", f"# Sales summary\n\n{narrative}\n",
                      {"figures.json": json.dumps(table, indent=2)}, memory_md=facts)


_LINE = re.compile(r"^(?P<desc>.+?)\s*\|\s*(?P<qty>\d+)\s*\|\s*(?P<price>\d+(?:\.\d+)?)\s*$")


def invoice_extraction(ctx: Context) -> TaskResult:
    text = _find(ctx.files, suffix=".txt", contains="invoice").read_text(encoding="utf-8")

    def field(name: str) -> str:
        m = re.search(rf"^{name}:\s*(.+)$", text, re.M | re.I)
        return m.group(1).strip() if m else ""

    number = re.search(r"INVOICE\s+([A-Z]+-\d+)", text)
    items = []
    for line in text.splitlines():
        m = _LINE.match(line.strip())
        if m:
            qty, price = int(m["qty"]), float(m["price"])
            items.append({"description": m["desc"], "qty": qty, "unit_price": price,
                          "amount": round(qty * price, 2)})
    if not items:
        raise ExecutionError("no line items found in invoice")

    computed = round(sum(i["amount"] for i in items), 2)
    stated = float(field("Subtotal").replace("$", "").replace(",", "") or 0)
    issues = []
    if abs(computed - stated) > 0.01:
        issues.append(f"Line items add up to {_money(computed)} but the invoice states "
                      f"a subtotal of {_money(stated)} (difference {_money(stated - computed)}).")

    data = {"invoice_number": number.group(1) if number else "", "vendor": field("Vendor"),
            "date": field("Date"), "due": field("Due"), "line_items": items,
            "computed_subtotal": computed, "stated_subtotal": stated,
            "stated_total": field("Total"), "issues": issues}
    verdict = "**Check before paying:** " + issues[0] if issues else "Line items match the subtotal."
    md = (f"# Invoice {data['invoice_number']} from {data['vendor']}\n\n"
          f"Due {data['due']}. {len(items)} line items. {verdict}\n")
    return TaskResult("completed", md, {"invoice.json": json.dumps(data, indent=2)},
                      memory_md=f"- invoice {data['invoice_number']}; issues: {issues or 'none'}")


def inventory_check(ctx: Context) -> TaskResult:
    rows = _read_csv(_find(ctx.files, suffix=".csv", contains="inventory"))
    low = [r for r in rows if int(r["on_hand"]) < int(r["reorder_point"])]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=["sku", "item", "on_hand", "reorder_point", "suggested_order"])
    writer.writeheader()
    for r in low:
        writer.writerow({**{k: r[k] for k in ("sku", "item", "on_hand", "reorder_point")},
                         "suggested_order": 2 * int(r["reorder_point"]) - int(r["on_hand"])})
    listing = "\n".join(f"- {r['sku']} {r['item']}: {r['on_hand']} on hand "
                        f"(reorder at {r['reorder_point']})" for r in low) or "- nothing below reorder point"
    md = f"# Low stock\n\n{len(low)} of {len(rows)} items are below their reorder point:\n\n{listing}\n"
    return TaskResult("completed", md, {"reorder_list.csv": out.getvalue()}, memory_md=listing)


_PRICE = re.compile(r"\b([A-Z]{2}-\d{3})\b[^$\d]*\$?(\d+(?:\.\d{1,2})?)")


def data_update(ctx: Context) -> TaskResult:
    m = _PRICE.search(ctx.task.body)
    if not m:
        raise ExecutionError("could not find 'SKU ... $price' in the request")
    sku, new_price = m.group(1), float(m.group(2))
    path = _find(ctx.files, suffix=".csv", contains="price")
    rows = _read_csv(path)
    match = [r for r in rows if r["sku"] == sku]
    if not match:
        raise ExecutionError(f"SKU {sku} not in {path.name}")
    old = match[0]["price"]
    match[0]["price"] = f"{new_price:.2f}"
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    change = f"{sku} ({match[0]['item']}): {old} -> {new_price:.2f}"
    return TaskResult("completed", f"# Price list updated\n\n- {change}\n",
                      {path.name: out.getvalue(), "changes.json": json.dumps(
                          [{"sku": sku, "old": old, "new": f"{new_price:.2f}"}])},
                      memory_md=f"- changed {change}")


def draft_reply(ctx: Context) -> TaskResult:
    draft = ctx.llm.complete(f"Draft a reply to this customer email.\nFACTS:\n{ctx.task.text}",
                             purpose="write")
    md = ("# Draft reply (not sent)\n\nSaved for a person to review and send.\n\n"
          f"---\n\n{draft}\n")
    return TaskResult("completed", md, {"draft_reply.md": draft},
                      memory_md="- drafted a reply; awaiting human send")


def schedule_meeting(ctx: Context) -> TaskResult:
    ics = ("BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\n"
           f"SUMMARY:{ctx.task.subject}\nDESCRIPTION:{ctx.task.body.splitlines()[0]}\n"
           "END:VEVENT\nEND:VCALENDAR\n")
    return TaskResult("completed", "# Meeting invite created\n\n(Sending is simulated in this demo.)\n",
                      {"invite.ics": ics}, memory_md="- created meeting invite")


def general_question(ctx: Context) -> TaskResult:
    return TaskResult("completed",
                      "# Needs more detail\n\nThe request was unclear, so a clarification "
                      "was drafted instead of guessing.\n",
                      memory_md="- asked sender for clarification")


EXECUTORS: dict[str, Callable[[Context], TaskResult]] = {
    "sales_summary": sales_summary,
    "invoice_extraction": invoice_extraction,
    "inventory_check": inventory_check,
    "data_update": data_update,
    "draft_reply": draft_reply,
    "schedule_meeting": schedule_meeting,
    "general_question": general_question,
}
