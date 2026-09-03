import json
import re
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

app = FastAPI(title="Alex Candidate Scorer", version="1.1.0")
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

SYSTEM_PROMPT = """You are an evidence-based sourcing assistant for a Senior C++ Software Engineer role at EGYM.
Use only explicit profile evidence.

CORE REQUIREMENTS
1) C++ EXPERIENCE
Target: about 3-5+ years substantial hands-on C++ in regular professional software-engineering roles.

IMPORTANT EXPERIENCE RULE:
Do NOT count internships, working-student / Werkstudent roles, student jobs, thesis work, university projects, student research, or similar student experience toward the required 3-5 years.
They may provide skill evidence, but contribute 0 years to professional experience duration.
Count only regular professional employment such as full-time/part-time Software Engineer, Developer, Senior Engineer, etc.

MET: about 3+ qualifying professional years, multiple substantial regular C++ roles, or clearly deep/current senior C++ experience with sufficient professional duration.
PARTIAL: about 1-3 qualifying professional years, C++ secondary, old, or duration unclear.
NOT_MET: no meaningful qualifying professional C++ experience.

2) HIGH-LEVEL / APPLICATION DEVELOPMENT
Strong evidence: application development, business/domain logic, desktop/GUI/product software, software architecture, feature development, design patterns, DDD.
MET: clear hands-on application/business-logic-heavy development.
PARTIAL: mixed low/high-level work or weak application evidence.

HARD SKIP
Skip when the main profile is ECU, device-controller, microcontroller, AUTOSAR ECU, BSP/driver-heavy, low-level hardware control or firmware-only work AND there is no strong application/business-logic depth.
Do not skip merely for having some embedded experience.

NICE TO HAVE ONLY
Qt/QML and CMake. Missing them is not a reason to skip a strong C++ application candidate.

DECISIONS
Strong C++ + strong application/business logic => OUTREACH / STRONG OUTREACH.
Strong C++ + unclear application layer => REVIEW.
Predominantly ECU/device-controller/firmware => SKIP.
Weak qualifying C++ => SKIP or REVIEW.

Missing info = unknown. Use partial when evidence is relevant but insufficient.
Ignore protected/personal characteristics. Do not assess culture fit.
Keep evidence very short and factual. Summary under 25 words.

Return JSON only:
{
  "summary":"...",
  "hard_skip":false,
  "hard_skip_reason":"",
  "criteria":[
    {"key":"cpp_depth","status":"met|partial|not_met|unknown","evidence":"..."},
    {"key":"application_layer","status":"met|partial|not_met|unknown","evidence":"..."},
    {"key":"qt_qml","status":"met|partial|not_met|unknown","evidence":"..."},
    {"key":"cmake","status":"met|partial|not_met|unknown","evidence":"..."}
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
        evidence = str(raw.get("evidence", "")).strip() or "Not visible in profile."
        criteria.append(Criterion(
            key=definition["key"],
            label=definition["label"],
            status=status,
            points=points,
            max_points=max_points,
            evidence=evidence[:180],
        ))

    by_key = {item.key: item for item in criteria}
    score = sum(item.points for item in criteria)

    if payload.get("hard_skip") is True:
        decision = "SKIP"
        score = min(score, 49)
        summary = str(payload.get("hard_skip_reason", "")).strip() or "Predominantly low-level ECU/device-controller/firmware profile."
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

        summary = str(payload.get("summary", "")).strip() or "Assessment based on the loaded LinkedIn profile."

    return ScoreResult(score=score, decision=decision, summary=summary[:200], criteria=criteria)


@app.get("/health")
def health():
    return {"ok": True, "model": OLLAMA_MODEL}


@app.post("/analyze", response_model=ScoreResult)
def analyze(profile: Profile):
    user_prompt = (
        "Evaluate this LinkedIn candidate profile. Ignore navigation, messages, recommendations and unrelated UI text.\n\n"
        f"PROFILE:\n{profile.visibleProfileText}"
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
            "num_ctx": 3072,
            "num_predict": 300,
        },
    }

    try:
        with httpx.Client(timeout=90.0) as client:
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
