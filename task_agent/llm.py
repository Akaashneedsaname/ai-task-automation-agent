"""LLM providers.

`MockProvider` is deterministic and needs no network or API key, so the whole
pipeline (and the test suite) runs offline. Real providers are optional and are
only imported when selected with LLM_PROVIDER.
"""

from __future__ import annotations

import json
import os
import re
from typing import Protocol


class LLMProvider(Protocol):
    name: str

    def complete(self, prompt: str, *, purpose: str) -> str:
        """Return the model's text response. `purpose` is "classify" or "write"."""
        ...


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------

_FOLLOW_UP_WORDS = ("also", "that", "same", "again", "follow up", "follow-up", "as well")


class MockProvider:
    """Keyword-based stand-in for an LLM.

    It returns the same JSON shape a real model is asked for, so the parsing,
    validation and gating code paths are exercised exactly as in production.
    """

    name = "mock"

    def complete(self, prompt: str, *, purpose: str) -> str:
        if purpose == "classify":
            return self._classify(prompt)
        return self._write(prompt)

    # -- classification -----------------------------------------------------
    def _classify(self, prompt: str) -> str:
        email = prompt.split("EMAIL:", 1)[-1].lower()
        memory = prompt.split("EMAIL:", 1)[0].lower()

        def result(intent, summary, plan, minutes, actions, confidence=0.9):
            return json.dumps(
                {
                    "intent": intent,
                    "summary": summary,
                    "plan": plan,
                    "estimated_minutes": minutes,
                    "actions": actions,
                    "confidence": confidence,
                }
            )

        prior = re.search(r"intent:\s*([a-z_]+)", memory)
        if prior and any(w in email for w in _FOLLOW_UP_WORDS):
            return result(
                prior.group(1),
                "Follow-up on an earlier request in the same thread.",
                ["Load thread memory", "Reuse earlier attachments", "Answer the new question"],
                5,
                [],
                0.85,
            )
        if re.search(r"\b(drop|truncate)\b|delete (all|every)", email):
            return result(
                "data_update",
                "Destructive change to stored data.",
                ["Run the requested destructive statement"],
                5,
                ["delete_records"],
            )
        if "forecast" in email or "quarterly" in email:
            return result(
                "sales_summary",
                "Large multi-region forecasting analysis.",
                ["Collect all regional data", "Build forecast", "Write report"],
                120,
                [],
            )
        if "sales" in email and any(w in email for w in ("summary", "report", "total")):
            return result(
                "sales_summary",
                "Summarise the attached weekly sales data.",
                ["Read sales CSV", "Compute totals by region", "Write short summary"],
                5,
                [],
            )
        if "invoice" in email:
            return result(
                "invoice_extraction",
                "Extract and check the attached invoice.",
                ["Parse invoice fields", "Check line items add up to the total"],
                5,
                [],
            )
        if any(w in email for w in ("update the price", "change the price", "set the price")):
            return result(
                "data_update",
                "Change a price in the price list.",
                ["Find the SKU", "Change its price", "Save the updated list"],
                5,
                ["update_records"],
            )
        if any(w in email for w in ("meeting", "schedule", "calendar")):
            return result(
                "schedule_meeting",
                "Create a meeting invite.",
                ["Work out attendees and time", "Create calendar invite"],
                5,
                ["send_calendar_invite"],
            )
        if any(w in email for w in ("stock", "inventory", "reorder")):
            return result(
                "inventory_check",
                "Find items below their reorder point.",
                ["Read inventory CSV", "Compare on-hand to reorder point"],
                5,
                [],
            )
        if any(w in email for w in ("reply", "respond", "draft", "complaint")):
            return result(
                "draft_reply",
                "Draft a reply for a human to review.",
                ["Read the customer message", "Draft a polite reply"],
                5,
                [],
            )
        return result(
            "general_question",
            "Unclear request.",
            ["Ask the sender for details"],
            10,
            [],
            0.35,
        )

    # -- writing ------------------------------------------------------------
    def _write(self, prompt: str) -> str:
        facts = prompt.split("FACTS:", 1)[-1].strip()
        if prompt.startswith("Draft a reply"):
            return (
                "Hi,\n\nThank you for letting us know, and I'm sorry for the trouble. "
                "I've passed the details to our team and we'll follow up with an update "
                "within one business day.\n\nBest regards,\nCustomer Support"
            )
        return f"Summary based on the computed figures:\n\n{facts}"


# ---------------------------------------------------------------------------
# Optional real providers
# ---------------------------------------------------------------------------

class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str = ""):
        import anthropic  # optional dependency

        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.model = model or "claude-sonnet-5-5"

    def complete(self, prompt: str, *, purpose: str) -> str:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in msg.content if block.type == "text")


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str = ""):
        import openai  # optional dependency

        self.client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.model = model or "gpt-4.1-mini"

    def complete(self, prompt: str, *, purpose: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content or ""


def get_provider(name: str, model: str = "") -> LLMProvider:
    name = (name or "mock").lower()
    if name == "mock":
        return MockProvider()
    if name == "anthropic":
        return AnthropicProvider(model)
    if name == "openai":
        return OpenAIProvider(model)
    raise ValueError(f"Unknown LLM_PROVIDER {name!r} (use mock, anthropic or openai)")
