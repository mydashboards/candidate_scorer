import json
import re
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

app = FastAPI(title="Alex Candidate Scorer", version="1.4.0")
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
    {"key": "cpp_depth", "label": "C++ professional experience", "max_points": 40},
    {"key": "application_logic", "label": "Application / business logic", "max_points": 25},
    {"key": "architecture", "label": "Architecture / software design", "max_points": 10},
    {"key": "egym_fit", "label": "EGYM product-software fit", "max_points": 15},
    {"key": "qt_qml", "label": "Qt / QML", "max_points": 5},
    {"key": "cmake", "label": "CMake", "max_points": 5},
]
STATUS_MULTIPLIER = {"met": 1.0, "partial": 0.5, "not_met": 0.0, "unknown": 0.0}

SYSTEM_PROMPT = """You are an evidence-based sourcing assistant for a Senior C++ Software Engineer role at EGYM. Evaluate PROFESSIONAL EXPERIENCE, especially job descriptions. Use only explicit evidence.

ROLE CONTEXT — WHAT EGYM NEEDS
The engineer will work on the large, long-lived C++ software platform running EGYM fitness machines. This is not a small firmware-only controller role. The software combines physical fitness hardware with substantial application/product software and complex business/domain logic. Engineers build features, maintain and evolve a large codebase, design architecture, test and deploy software, and collaborate with sports science, electrical engineering and SRE.

Think of the ideal transferable background as: C++ product/application engineering for a complex real-world system, ideally software that interacts with hardware/devices while still containing a substantial high-level application layer, domain/business logic, architecture and many product features.

1) PROFESSIONAL C++ EXPERIENCE — HARD MINIMUM
At least 3 years substantial hands-on C++ in regular professional employment. Target 3-5+ years.
MET = profile supports >=3 qualifying professional C++ years.
PARTIAL = some professional C++, but <3 years or insufficient evidence for 3 years.
NOT_MET = no meaningful professional hands-on C++.
UNKNOWN = duration cannot be determined.
Internships, Werkstudent/working-student roles, student jobs, thesis, university projects and student research count as ZERO years.
Do not mark MET from seniority, a skill list, company or title alone.

2) APPLICATION / BUSINESS LOGIC — HARD REQUIREMENT
Ask: WHAT did the person build with C++?
Strong signals: application/product software, business/domain logic, complex product features, workflows, state management, data processing, APIs/services, desktop/GUI applications, application-layer modules, substantial legacy-application modernization, or ownership of a complex application.
MET = clear substantial hands-on application/business/domain-logic development.
PARTIAL = some high-level evidence but mixed/weak/unclear.
NOT_MET = clearly essentially low-level only.
UNKNOWN = job descriptions do not reveal the layer.
Qt/QML can support this but is not required.

3) ARCHITECTURE / SOFTWARE DESIGN
Positive evidence: software architecture, component/module design, system decomposition, interfaces/APIs, design patterns, DDD, maintainable structures, large refactorings, legacy modernization, technical design decisions, ownership of large software components.
MET = explicit strong architecture/design ownership.
PARTIAL = some design/refactoring/component ownership.
UNKNOWN = not described.

4) EGYM PRODUCT-SOFTWARE FIT
Estimate how transferable the candidate's PROFESSIONAL SOFTWARE EXPERIENCE is to EGYM's fitness-machine software. Do NOT judge personality or culture fit.

Strong EGYM-fit signals include combinations of:
- large or long-lived C++ application/product codebases
- complex business/domain logic
- many interconnected product features
- application software interacting with physical devices/hardware
- desktop/HMI/GUI software controlling or configuring complex products
- medical devices, robotics, industrial machines, instrumentation, smart devices or similar physical products IF the work includes a substantial high-level application layer
- architecture of complex C++ systems
- modernization/refactoring of large legacy applications
- reliability, testing, deployment and maintainability of production software
- cross-functional development with hardware/electrical/domain experts
- ownership across feature development rather than isolated low-level components

MET = background is strongly transferable to a large, business-logic-heavy C++ product running on connected physical fitness machines.
PARTIAL = some transferable signals, but scale/product/application depth is unclear or only partly comparable.
NOT_MET = professional background is clearly dominated by work unlike EGYM's application/product layer, especially isolated low-level controller/firmware work.
UNKNOWN = profile does not provide enough detail. Do NOT invent large-scale experience merely because the employer is large.

IMPORTANT: 'large scale' here means a substantial/complex/long-lived software product or codebase with many features/components and domain logic. It does NOT require web-scale traffic, distributed cloud systems or millions of requests.

5) LOW-LEVEL DOMINANCE — HARD SKIP CHECK
Strong low-level signals: ECU, AUTOSAR ECU, device controllers, firmware, microcontrollers, BSP, drivers, register-level work, hardware abstraction, sensor/actuator control, low-level hardware control, CAN/LIN when this is the core job.
Do not hard-skip merely because someone has embedded/device/hardware experience. Hardware-adjacent C++ can be highly relevant to EGYM if there is a substantial application/product layer.
Hard skip ONLY when low-level ECU/device-controller/firmware work is dominant AND there is no strong application/business-logic depth.

Examples:
- C++/Qt application for a medical/industrial device, complex workflows and architecture => STRONG EGYM fit.
- Large C++ desktop/product application with complex domain logic but no hardware => GOOD EGYM fit.
- Robotics software with high-level application/behavior/domain logic and architecture => GOOD EGYM fit.
- AUTOSAR ECU components, drivers and CAN/hardware abstraction only => POOR EGYM fit / likely SKIP.
- 'C++ software for devices' without details => UNKNOWN/REVIEW, not automatic skip.

6) NICE TO HAVE ONLY
Qt/QML and CMake. Missing them is not a reason to reject a strong C++ application candidate.

DECISION RULES
- <3 years qualifying professional C++ => never OUTREACH.
- >=3 years C++ + strong application/business logic + strong EGYM transferability => STRONG OUTREACH.
- >=3 years C++ + strong application/business logic + reasonable/partial EGYM transferability => OUTREACH.
- >=3 years C++ + application layer or EGYM transferability unclear => REVIEW.
- predominantly low-level ECU/device-controller/firmware with no strong application depth => SKIP.

Missing information = unknown. Never invent evidence. Ignore protected/personal characteristics and personality/culture fit. Keep evidence very short. Summary under 25 words and explain whether the person's experience transfers to EGYM fitness-machine software.

Return JSON only:
{"summary":"...","hard_skip":false,"hard_skip_reason":"","criteria":[{"key":"cpp_depth","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"application_logic","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"architecture","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"egym_fit","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"qt_qml","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"cmake","status":"met|partial|not_met|unknown","evidence":"..."}]}
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
        egym = by_key["egym_fit"].status

        if cpp == "not_met":
            score = min(score, 39)
            decision = "SKIP"
        elif cpp in ("partial", "unknown"):
            score = min(score, 64)
            decision = "REVIEW"
        elif application == "not_met":
            score = min(score, 49)
            decision = "SKIP"
        elif application == "unknown" or egym == "unknown":
            score = min(score, 69)
            decision = "REVIEW"
        elif application == "partial" and architecture not in ("met", "partial"):
            score = min(score, 69)
            decision = "REVIEW"
        elif application == "met" and egym == "met":
            decision = "STRONG OUTREACH" if score >= 85 else "OUTREACH"
        elif application == "met" and egym == "partial":
            decision = "OUTREACH"
        elif application == "partial" and architecture == "met" and egym in ("met", "partial"):
            decision = "OUTREACH"
        else:
            decision = "REVIEW"

        summary = str(payload.get("summary", "")).strip() or "Assessment based on transferability to EGYM fitness-machine product software."

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
            {"role": "user", "content": "Evaluate professional experience for transferability to EGYM fitness-machine software: C++ duration, application/business logic, architecture, complex product/codebase experience, hardware-adjacent product software, and low-level dominance.\n\n" + profile.visibleProfileText},
        ],
        "options": {"temperature": 0, "num_ctx": 3072, "num_predict": 380},
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
