import json
import re
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

app = FastAPI(title="Alex Candidate Scorer", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = "qwen3:4b"


class Profile(BaseModel):
    name: str
    profileUrl: str
    visibleProfileText: str = Field(min_length=20, max_length=50000)
    capturedAt: str


class Criterion(BaseModel):
    key: str
    label: str
    status: Literal["met", "partial", "not_met", "unknown"]
    points: int
    max_points: int
    evidence: str


class ScoreResult(BaseModel):
    score: int = Field(ge=0, le=100)
    decision: Literal["STRONG OUTREACH", "OUTREACH", "REVIEW", "SKIP"]
    summary: str
    criteria: list[Criterion]


CRITERIA = [
    {"key": "cpp_depth", "label": "C++ depth", "max_points": 50},
    {"key": "application_layer", "label": "Application / business logic depth", "max_points": 40},
    {"key": "qt_qml", "label": "Qt / QML", "max_points": 5},
    {"key": "cmake", "label": "CMake", "max_points": 5},
]

STATUS_MULTIPLIER = {
    "met": 1.0,
    "partial": 0.5,
    "not_met": 0.0,
    "unknown": 0.0,
}

SYSTEM_PROMPT = """You are an evidence-based sourcing assistant. Decide whether the visible LinkedIn profile is worth outreach for a Senior C++ Software Engineer role at EGYM.

Use only explicit profile evidence.

GOAL
A strong C++ engineer with application-layer, software-architecture, or business-logic depth should move forward even if Qt/QML or CMake are missing.

HARD REQUIREMENTS

1. C++ EXPERIENCE
Target: approximately 3-5+ years of substantial hands-on C++ software engineering.

MET:
- approximately 3+ years of hands-on C++
- OR multiple substantial C++ roles
- OR clear senior-level / deep modern C++ expertise

PARTIAL:
- approximately 1-3 years of C++
- C++ is only secondary
- duration or depth is unclear
- C++ experience is old

NOT_MET:
- no meaningful hands-on C++ evidence

2. HIGH-LEVEL / APPLICATION DEVELOPMENT
The candidate should primarily work above the low-level hardware/control layer.

Strong evidence includes:
- application development
- application layer
- business logic
- desktop software
- GUI applications
- complex product software
- software architecture
- domain logic
- feature development
- design patterns
- domain-driven design

MET:
- clear hands-on application-layer or business-logic-heavy software development

PARTIAL:
- mixed high-level and low-level work
- weak but relevant application/architecture evidence

HARD SKIP
Skip profiles that are predominantly focused on:
- ECU development
- device controller development
- microcontroller-centric development
- low-level hardware control
- AUTOSAR ECU work
- BSP / driver-heavy work
- firmware-only work

Do NOT hard-skip someone merely because they have embedded experience.
Only hard-skip if low-level ECU/device-controller/firmware work is the main professional profile and there is no strong application/business-logic depth.

NICE TO HAVE
- Qt / QML
- CMake

These are bonuses only. Do not significantly downgrade a strong C++ application-layer candidate because Qt/QML or CMake are missing.

DECISION PRINCIPLES
- Strong C++ + strong application/business-logic depth => OUTREACH or STRONG OUTREACH
- Strong C++ + unclear application layer => REVIEW
- Strong C++ + predominantly ECU/device-controller/firmware => SKIP
- Weak C++ => SKIP or REVIEW depending on evidence
- Missing Qt/QML => not a reason to skip
- Missing CMake => not a reason to skip

GENERAL
Missing information = unknown.
Use partial for relevant but insufficient evidence.
Do not infer skills from company names alone.
Ignore protected and personal characteristics.
Do not assess personality or culture fit.
Keep evidence concise and factual.
Keep the summary under 35 words.

Return JSON with exactly these fields:
{
  "summary": "short explanation",
  "hard_skip": false,
  "hard_skip_reason": "",
  "criteria": [
    {"key": "cpp_depth", "status": "met|partial|not_met|unknown", "evidence": "..."},
    {"key": "application_layer", "status": "met|partial|not_met|unknown", "evidence": "..."},
    {"key": "qt_qml", "status": "met|partial|not_met|unknown", "evidence": "..."},
    {"key": "cmake", "status": "met|partial|not_met|unknown", "evidence": "..."}
  ]
}
"""


def _clean_json_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("The local model did not return valid JSON.")
    return text[start:end + 1]


def _normalise_result(payload: dict) -> ScoreResult:
    incoming = {item.get("key"): item for item in payload.get("criteria", [])}
    criteria = []

    for definition in CRITERIA:
        raw = incoming.get(definition["key"], {})
        status = raw.get("status", "unknown")
        if status not in STATUS_MULTIPLIER:
            status = "unknown"

        max_points = definition["max_points"]
        points = round(max_points * STATUS_MULTIPLIER[status])
        evidence = str(raw.get("evidence", "")).strip() or "Not visible in the loaded profile."

        criteria.append(Criterion(
            key=definition["key"],
            label=definition["label"],
            status=status,
            points=points,
            max_points=max_points,
            evidence=evidence[:260],
        ))

    by_key = {item.key: item for item in criteria}
    score = sum(item.points for item in criteria)

    if payload.get("hard_skip") is True:
        decision = "SKIP"
        score = min(score, 49)
        summary = str(payload.get("hard_skip_reason", "")).strip() or "Skip: predominantly low-level ECU/device-controller/firmware profile."
    else:
        if by_key["cpp_depth"].status == "not_met":
            score = min(score, 39)
        elif by_key["cpp_depth"].status == "partial":
            score = min(score, 64)

        if by_key["application_layer"].status == "not_met":
            score = min(score, 49)
        elif by_key["application_layer"].status == "partial":
            score = min(score, 69)

        if score >= 90:
            decision = "STRONG OUTREACH"
        elif score >= 75:
            decision = "OUTREACH"
        elif score >= 60:
            decision = "REVIEW"
        else:
            decision = "SKIP"

        summary = str(payload.get("summary", "")).strip() or "Assessment based on the currently loaded LinkedIn profile."

    return ScoreResult(
        score=score,
        decision=decision,
        summary=summary[:260],
        criteria=criteria,
    )


@app.get("/health")
def health():
    return {"ok": True, "model": OLLAMA_MODEL}


@app.post("/analyze", response_model=ScoreResult)
def analyze(profile: Profile):
    user_prompt = (
        "Evaluate this candidate profile. Ignore LinkedIn navigation, messages, recommendations, "
        "and unrelated interface text.\n\n"
        f"VISIBLE PROFILE TEXT:\n{profile.visibleProfileText}"
    )

    request_body = {
        "model": OLLAMA_MODEL,
        "keep_alive": "60m",
        "stream": False,
        "think": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "options": {
            "temperature": 0,
            "num_ctx": 4096,
            "num_predict": 420,
        },
    }

    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(OLLAMA_URL, json=request_body)
            response.raise_for_status()

        content = response.json().get("message", {}).get("content", "")
        payload = json.loads(_clean_json_text(content))
        return _normalise_result(payload)

    except httpx.ConnectError as exc:
        raise HTTPException(503, "Ollama is not reachable. Open Ollama and make sure qwen3:4b is installed.") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f"Ollama error: {exc.response.text[:500]}") from exc
    except (json.JSONDecodeError, ValueError, ValidationError) as exc:
        raise HTTPException(502, f"Could not read the model response: {exc}") from exc
    except Exception as exc:
        raise HTTPException(500, f"Analysis failed: {exc}") from exc
