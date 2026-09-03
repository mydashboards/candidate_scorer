import json
import re
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

app = FastAPI(title="Alex Candidate Scorer", version="1.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["POST", "GET"], allow_headers=["*"])

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
    {"key": "cpp_depth", "label": "C++ professional experience", "max_points": 50},
    {"key": "application_layer", "label": "Application / business logic depth", "max_points": 40},
    {"key": "qt_qml", "label": "Qt / QML", "max_points": 5},
    {"key": "cmake", "label": "CMake", "max_points": 5},
]
STATUS_MULTIPLIER = {"met": 1.0, "partial": 0.5, "not_met": 0.0, "unknown": 0.0}

SYSTEM_PROMPT = """You are an evidence-based sourcing assistant for a Senior C++ Software Engineer role at EGYM. Use only explicit profile evidence.

HARD REQUIREMENT 1 — PROFESSIONAL C++ EXPERIENCE
The candidate needs AT LEAST 3 years of substantial hands-on C++ in regular professional software-engineering employment. The target range is 3-5+ years.

This is a hard minimum:
- MET = at least 3 qualifying professional years of substantial hands-on C++ are explicitly supported by the profile.
- PARTIAL = some qualifying professional C++ exists, but it is less than 3 years OR the profile does not provide enough evidence to establish at least 3 years.
- NOT_MET = no meaningful qualifying professional hands-on C++.
- UNKNOWN = professional C++ duration cannot be determined.

Do NOT mark C++ as MET merely because the candidate is senior, has a C++ skill listed, or has multiple software roles. There must be evidence supporting at least 3 years of qualifying professional C++.

EXPERIENCE COUNTING RULE
Internships, working-student/Werkstudent roles, student jobs, thesis work, university projects, student research and similar student experience count as ZERO years toward the 3-year minimum. They may show skill exposure only.
Count regular professional employment such as Software Engineer, Developer, Senior Engineer, etc.

HARD REQUIREMENT 2 — HIGH-LEVEL / APPLICATION DEVELOPMENT
Candidate should have hands-on application/business-logic-heavy development. Positive evidence: application development, business/domain logic, desktop/GUI/product software, software architecture, feature development, design patterns, DDD.
MET = clear application/business-logic-heavy work.
PARTIAL = mixed low/high-level work or weak evidence.

HARD SKIP
Skip when the main profile is ECU, device-controller, microcontroller, AUTOSAR ECU, BSP/driver-heavy, low-level hardware control or firmware-only work AND there is no strong application/business-logic depth. Do not skip merely for some embedded experience.

NICE TO HAVE ONLY
Qt/QML and CMake. Missing either is not a reason to skip.

DECISION
- Less than 3 years qualifying professional C++ => cannot be OUTREACH.
- At least 3 years C++ + strong application/business logic => OUTREACH / STRONG OUTREACH.
- At least 3 years C++ + unclear application layer => REVIEW.
- Predominantly ECU/device-controller/firmware => SKIP.

Missing info = unknown. Do not infer skills from company names. Ignore protected/personal characteristics. Keep evidence very short. Summary under 25 words.

Return JSON only:
{"summary":"...","hard_skip":false,"hard_skip_reason":"","criteria":[{"key":"cpp_depth","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"application_layer","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"qt_qml","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"cmake","status":"met|partial|not_met|unknown","evidence":"..."}]}
"""

def _clean_json_text(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
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
        criteria.append(Criterion(key=definition["key"], label=definition["label"], status=status, points=points, max_points=max_points, evidence=evidence[:180]))

    by_key = {item.key: item for item in criteria}
    score = sum(item.points for item in criteria)

    if payload.get("hard_skip") is True:
        decision = "SKIP"
        score = min(score, 49)
        summary = str(payload.get("hard_skip_reason", "")).strip() or "Predominantly low-level ECU/device-controller/firmware profile."
    else:
        cpp_status = by_key["cpp_depth"].status
        app_status = by_key["application_layer"].status

        # The 3-year professional C++ minimum is a hard gate.
        if cpp_status == "not_met":
            score = min(score, 39)
            decision = "SKIP"
        elif cpp_status in ("partial", "unknown"):
            score = min(score, 64)
            decision = "REVIEW"
        elif app_status == "not_met":
            score = min(score, 49)
            decision = "SKIP"
        elif app_status in ("partial", "unknown"):
            score = min(score, 69)
            decision = "REVIEW"
        elif score >= 90:
            decision = "STRONG OUTREACH"
        else:
            decision = "OUTREACH"

        summary = str(payload.get("summary", "")).strip() or "Assessment based on professional experience shown on LinkedIn."

    return ScoreResult(score=score, decision=decision, summary=summary[:200], criteria=criteria)

@app.get("/health")
def health():
    return {"ok": True, "model": OLLAMA_MODEL}

@app.post("/analyze", response_model=ScoreResult)
def analyze(profile: Profile):
    request_body = {
        "model": OLLAMA_MODEL,
        "keep_alive": "60m",
        "stream": False,
        "think": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Evaluate this profile. Focus on PROFESSIONAL EXPERIENCE.\n\n" + profile.visibleProfileText},
        ],
        "options": {"temperature": 0, "num_ctx": 3072, "num_predict": 300},
    }
    try:
        with httpx.Client(timeout=90.0) as client:
            response = client.post(OLLAMA_URL, json=request_body)
            response.raise_for_status()
        content = response.json().get("message", {}).get("content", "")
        return _normalise_result(json.loads(_clean_json_text(content)))
    except httpx.ConnectError as exc:
        raise HTTPException(503, "Ollama is not reachable. Open Ollama and make sure qwen3:4b is installed.") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f"Ollama error: {exc.response.text[:500]}") from exc
    except (json.JSONDecodeError, ValueError, ValidationError) as exc:
        raise HTTPException(502, f"Could not read the model response: {exc}") from exc
    except Exception as exc:
        raise HTTPException(500, f"Analysis failed: {exc}") from exc
