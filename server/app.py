import json
import re
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

app = FastAPI(title="Alex Candidate Scorer", version="1.1.2-stable")
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
STATUS_MULTIPLIER = {"met":1.0,"partial":0.5,"not_met":0.0,"unknown":0.0}

SYSTEM_PROMPT = """You are an evidence-based sourcing assistant for a Senior C++ Software Engineer role at EGYM. Use only explicit profile evidence.

CORE REQUIREMENTS
1) C++ EXPERIENCE
Target: about 3-5+ years substantial hands-on C++ in regular professional software-engineering roles.
A Skills line shown INSIDE a specific LinkedIn Experience job belongs to that job and can prove that C++ was used in that role. Do not use the separate global LinkedIn Skills section.

CRITICAL DURATION RULE:
Do NOT automatically assign the full duration of a multi-project job to C++ when C++ is explicitly mentioned only for one project/client/product within that job. If a 4-year job lists many projects and only one project says C++, C++ is proven but its duration is UNKNOWN unless the project duration is stated or C++ is clearly described as a general technology of the whole role.
If the job-level Skills line itself lists C++ for the overall job, that is stronger evidence that C++ belongs to the role, but still avoid claiming an exact number of C++ years unless the profile supports it.
Never invent '4+ years C++' from a 4-year employment merely because one project used C++.

Do NOT count internships, working-student/Werkstudent roles, student jobs, thesis work, university projects or student research toward required years. Count regular professional employment.
MET: 3+ qualifying professional C++ years are reasonably supported.
PARTIAL: meaningful professional C++ exists but is under 3 years OR exact duration is unclear because C++ is tied only to individual projects.
NOT_MET: no meaningful qualifying professional C++.

2) HIGH-LEVEL / APPLICATION DEVELOPMENT
Strong evidence: application development, business/domain logic, desktop/GUI/product software, software architecture, feature development, design patterns, DDD, complex application maintenance or product features. Do not require the literal words business logic.
MET: clear hands-on application/business-logic-heavy development. PARTIAL: mixed low/high-level work or weak application evidence.

EGYM CONTEXT / EXCEPTIONAL TRANSFERABILITY
EGYM engineers work on a large, long-lived C++ product for fitness machines with substantial application/domain logic, many features/components, UI and hardware interaction. Complex C++ application/product software, Qt/HMI/GUI, medical/industrial/device application software and large product codebases are especially transferable. Large-scale means complex/long-lived software, not web traffic.

A candidate with C++ duration below 3 years or unclear may STILL be worth OUTREACH when the visible experience is exceptionally close to EGYM. Exceptional fit means a combination such as: professional C++ + Qt/QML + CMake/Linux + substantial high-level application/product development + device/machine/medical/industrial/real-time software or similarly relevant complex domain software.
This exception is for sourcing: do not reject a highly transferable candidate solely because LinkedIn does not state the exact duration of a very relevant C++ project.

HARD SKIP
Skip when the main profile is ECU, device-controller, microcontroller, AUTOSAR ECU, BSP/driver-heavy, low-level hardware control or firmware-only AND there is no strong application/business-logic depth. Do not skip merely for embedded or hardware-adjacent experience.

NICE TO HAVE ONLY
Qt/QML and CMake. If Qt, Qt Quick or QML is explicitly stated in a job description, mark Qt/QML MET. It does NOT need to appear in a Skills line. Likewise, explicit CMake in a job description is sufficient for CMake MET.

DECISIONS
3+ years C++ + strong application/business logic => OUTREACH / STRONG OUTREACH.
C++ duration under 3 years or unclear + ordinary fit => REVIEW.
C++ duration under 3 years or unclear + EXCEPTIONAL EGYM-transferable application background => OUTREACH is allowed.
Strong C++ + unclear application layer => REVIEW.
Predominantly ECU/device-controller/firmware => SKIP.
Missing info = unknown. Use partial when evidence is relevant but insufficient.
Keep evidence very short and factual. Never state an exact C++ duration that the profile does not support. Summary under 25 words.

Return JSON only:
{"summary":"...","hard_skip":false,"hard_skip_reason":"","exceptional_egym_fit":false,"criteria":[{"key":"cpp_depth","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"application_layer","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"qt_qml","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"cmake","status":"met|partial|not_met|unknown","evidence":"..."}]}
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
    criteria=[]
    for definition in CRITERIA:
        raw=incoming.get(definition["key"],{})
        status=raw.get("status","unknown")
        if status not in STATUS_MULTIPLIER: status="unknown"
        max_points=definition["max_points"]
        points=round(max_points*STATUS_MULTIPLIER[status])
        evidence=str(raw.get("evidence","")).strip() or "Not visible in profile."
        criteria.append(Criterion(key=definition["key"],label=definition["label"],status=status,points=points,max_points=max_points,evidence=evidence[:180]))
    by_key={item.key:item for item in criteria}; score=sum(item.points for item in criteria)
    if payload.get("hard_skip") is True:
        decision="SKIP"; score=min(score,49); summary=str(payload.get("hard_skip_reason","")).strip() or "Predominantly low-level ECU/device-controller/firmware profile."
    else:
        cpp=by_key["cpp_depth"].status; app=by_key["application_layer"].status
        exceptional = payload.get("exceptional_egym_fit") is True
        if cpp=="not_met": score=min(score,39)
        elif cpp in ("partial","unknown") and not exceptional: score=min(score,64)
        if app=="not_met": score=min(score,49)
        elif app in ("partial","unknown"): score=min(score,69)

        if cpp=="met" and app=="met" and score>=90:
            decision="STRONG OUTREACH"
        elif cpp=="met" and app=="met" and score>=75:
            decision="OUTREACH"
        elif cpp in ("partial","unknown") and app=="met" and exceptional:
            decision="OUTREACH"
        elif score>=50:
            decision="REVIEW"
        else:
            decision="SKIP"
        summary=str(payload.get("summary","")).strip() or "Assessment based on the loaded LinkedIn Experience."
    return ScoreResult(score=score,decision=decision,summary=summary[:200],criteria=criteria)

@app.get("/health")
def health(): return {"ok":True,"model":OLLAMA_MODEL,"mode":"stable","version":"1.1.2-stable"}

@app.post("/analyze",response_model=ScoreResult)
def analyze(profile: Profile):
    user_prompt="Evaluate this LinkedIn candidate profile. Focus on Experience, including Skills attached to individual jobs. Ignore unrelated UI text.\n\nPROFILE:\n"+profile.visibleProfileText
    request_body={"model":OLLAMA_MODEL,"keep_alive":"60m","stream":False,"think":False,"format":"json","messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":user_prompt}],"options":{"temperature":0,"num_ctx":3072,"num_predict":320}}
    try:
        with httpx.Client(timeout=90.0) as client:
            response=client.post(OLLAMA_URL,json=request_body); response.raise_for_status()
        content=response.json().get("message",{}).get("content","")
        return _normalise_result(json.loads(_clean_json_text(content)))
    except httpx.ConnectError as exc: raise HTTPException(503,"Ollama is not reachable. Open Ollama and make sure qwen3:4b is installed.") from exc
    except httpx.HTTPStatusError as exc: raise HTTPException(502,f"Ollama error: {exc.response.text[:500]}") from exc
    except (json.JSONDecodeError,ValueError,ValidationError) as exc: raise HTTPException(502,f"Could not read the model response: {exc}") from exc
    except Exception as exc: raise HTTPException(500,f"Analysis failed: {exc}") from exc
