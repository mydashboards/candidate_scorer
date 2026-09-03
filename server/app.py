import json
import re
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

app = FastAPI(title="Alex Candidate Scorer", version="1.3.0")
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
    {"key": "cpp_depth", "label": "C++ professional experience", "max_points": 45},
    {"key": "application_logic", "label": "Application / business logic", "max_points": 30},
    {"key": "architecture", "label": "Architecture / software design", "max_points": 15},
    {"key": "qt_qml", "label": "Qt / QML", "max_points": 5},
    {"key": "cmake", "label": "CMake", "max_points": 5},
]
STATUS_MULTIPLIER = {"met": 1.0, "partial": 0.5, "not_met": 0.0, "unknown": 0.0}

SYSTEM_PROMPT = """You are an evidence-based sourcing assistant for a Senior C++ Software Engineer role at EGYM. Evaluate PROFESSIONAL EXPERIENCE, especially job descriptions. Use only explicit evidence.

1) PROFESSIONAL C++ EXPERIENCE — HARD MINIMUM
At least 3 years substantial hands-on C++ in regular professional employment. Target 3-5+ years.
MET = profile supports >=3 qualifying professional C++ years.
PARTIAL = some professional C++, but <3 years or insufficient evidence for 3 years.
NOT_MET = no meaningful professional hands-on C++.
UNKNOWN = duration cannot be determined.
Do not mark MET merely from seniority, a skill list, or company/title.
Internships, Werkstudent/working-student roles, student jobs, thesis, university projects and student research count as ZERO years.

2) APPLICATION / BUSINESS LOGIC — HARD REQUIREMENT
Ask: WHAT did the person build with C++?
Strong HIGH-LEVEL positive evidence includes application software, desktop/product software, business logic, domain logic, product features, workflows, state management, data processing, APIs/services, GUI applications, complex user-facing or domain-facing functionality, application-layer modules, or ownership/refactoring of a substantial application.
MET = clear substantial hands-on application/business/domain-logic development.
PARTIAL = some high-level evidence but mixed/weak/unclear.
NOT_MET = evidence clearly shows work is essentially low-level only.
UNKNOWN = job descriptions do not reveal the development layer.
Qt/QML can support application-level evidence but is NOT required.

3) ARCHITECTURE / SOFTWARE DESIGN — SUPPORTING SIGNAL
Positive evidence: software architecture, architectural design, component/module design, system decomposition, interfaces/APIs, design patterns, DDD, maintainable application structure, major refactoring/reworking of legacy applications, technical design decisions, ownership of larger software components.
MET = explicit strong architecture/design ownership.
PARTIAL = some design/refactoring/component ownership.
UNKNOWN = not described. Architecture is valuable but not mandatory if application/business-logic evidence is already strong.

4) LOW-LEVEL DOMINANCE — HARD SKIP CHECK
Strong low-level signals: ECU development, AUTOSAR ECU, device controllers, firmware, microcontrollers, BSP, drivers, register-level work, hardware abstraction, sensor/actuator control, low-level hardware control, CAN/LIN work when this is the core job.
Do NOT hard-skip merely because a candidate has embedded experience, works with hardware, or develops software for devices.
Hard skip ONLY when low-level ECU/device-controller/firmware work is the dominant professional profile AND there is no strong application/business-logic depth.
Examples:
- 'C++ software for medical devices' alone => UNKNOWN application layer, not automatic skip.
- 'C++/Qt desktop application for configuring medical devices; application architecture and workflows' => strong application signal.
- 'AUTOSAR ECU components, CAN communication, hardware abstraction' => strong low-level signal.

5) NICE TO HAVE ONLY
Qt/QML and CMake. Missing them is not a reason to reject or substantially downgrade a strong C++ application candidate.

DECISION RULES
- <3 years qualifying professional C++ => never OUTREACH.
- >=3 years C++ + strong application/business logic => OUTREACH; architecture can strengthen to STRONG OUTREACH.
- >=3 years C++ + application layer unclear => REVIEW.
- >=3 years C++ + strong architecture plus credible high-level application evidence => OUTREACH.
- predominantly low-level ECU/device-controller/firmware with no strong application depth => SKIP.

Missing information = unknown, never invent evidence. Ignore protected/personal characteristics. Keep each evidence sentence very short. Summary under 25 words.

Return JSON only:
{"summary":"...","hard_skip":false,"hard_skip_reason":"","criteria":[{"key":"cpp_depth","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"application_logic","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"architecture","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"qt_qml","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"cmake","status":"met|partial|not_met|unknown","evidence":"..."}]}
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
        cpp = by_key["cpp_depth"].status
        application = by_key["application_logic"].status
        architecture = by_key["architecture"].status

        if cpp == "not_met":
            score = min(score, 39)
            decision = "SKIP"
        elif cpp in ("partial", "unknown"):
            score = min(score, 64)
            decision = "REVIEW"
        elif application == "not_met":
            score = min(score, 49)
            decision = "SKIP"
        elif application == "unknown":
            score = min(score, 69)
            decision = "REVIEW"
        elif application == "partial" and architecture not in ("met", "partial"):
            score = min(score, 69)
            decision = "REVIEW"
        elif application == "met":
            decision = "STRONG OUTREACH" if score >= 90 else "OUTREACH"
        elif application == "partial" and architecture == "met":
            decision = "OUTREACH"
        else:
            decision = "REVIEW"

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
            {"role": "user", "content": "Evaluate the professional experience. Determine C++ duration, what they build, application/business logic, architecture, and low-level dominance.\n\n" + profile.visibleProfileText},
        ],
        "options": {"temperature": 0, "num_ctx": 3072, "num_predict": 340},
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
