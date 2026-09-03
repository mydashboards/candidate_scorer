import json
import re
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

app = FastAPI(title="Alex Candidate Scorer", version="2.2.1")
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

CRITERIA=[{"key":"cpp_depth","label":"C++ professional experience","max_points":40},{"key":"application_logic","label":"Application / business logic","max_points":25},{"key":"architecture","label":"Architecture / software design","max_points":10},{"key":"egym_fit","label":"EGYM product-software fit","max_points":15},{"key":"qt_qml","label":"Qt / QML","max_points":5},{"key":"cmake","label":"CMake","max_points":5}]
MULT={"met":1.0,"partial":0.5,"not_met":0.0,"unknown":0.0}

SYSTEM_PROMPT="""Assess LinkedIn PROFESSIONAL EXPERIENCE for an EGYM Senior C++ Software Engineer. Use explicit evidence only.
A 'Skills:' line INSIDE a job entry belongs to that job and is evidence for technologies used in that job. Do not confuse it with the global Skills section. If C++ is in a regular professional job's description or job-level Skills line, count that job's displayed duration as C++ experience. Sum such jobs. Internships, Werkstudent/working-student, student jobs, thesis and university projects count zero. C++: met >=3 years; partial >0 but <3 years or insufficient duration; not_met only no qualifying C++; unknown only uninterpretable.
Application/business logic: judge what they built. Applications, product features, domain/business logic, workflows, UI/HMI/desktop, backend/services, data processing and substantial application maintenance are positive. Do not require literal 'business logic'.
Architecture: architecture/design ownership, modules/components/interfaces, patterns, refactoring, legacy modernization, maintainability. Missing mention = unknown, not not_met.
EGYM fit: EGYM has a large long-lived C++ product on fitness machines with many features/components, domain logic, UI, hardware interaction, testing and maintainability. Strong transfer: complex C++ application/product software, Qt/HMI/GUI, medical/industrial/robotics/device application software, large product codebases. Complex C++ application experience can fit even without hardware. Large-scale means complex/long-lived codebase, not web traffic.
Hard skip only if predominantly ECU/AUTOSAR/firmware/microcontroller/BSP/driver/controller low-level work with no substantial application layer. Embedded/hardware itself is not a skip. Qt/QML and CMake are nice-to-have only.
Summary under 20 words. Evidence short and factual."""

MODEL_SCHEMA={
 "type":"object","properties":{
  "summary":{"type":"string"},"hard_skip":{"type":"boolean"},"hard_skip_reason":{"type":"string"},
  "criteria":{"type":"array","items":{"type":"object","properties":{"key":{"type":"string","enum":["cpp_depth","application_logic","architecture","egym_fit","qt_qml","cmake"]},"status":{"type":"string","enum":["met","partial","not_met","unknown"]},"evidence":{"type":"string"}},"required":["key","status","evidence"]}}
 },"required":["summary","hard_skip","hard_skip_reason","criteria"]
}

def normalize(payload):
    incoming={x.get("key"):x for x in payload.get("criteria",[]) if isinstance(x,dict)}; criteria=[]
    for d in CRITERIA:
        raw=incoming.get(d["key"],{}); status=raw.get("status","unknown")
        if status not in MULT: status="unknown"
        evidence=str(raw.get("evidence","")).strip() or "Not visible in Experience."
        criteria.append(Criterion(key=d["key"],label=d["label"],status=status,points=round(d["max_points"]*MULT[status]),max_points=d["max_points"],evidence=evidence[:220]))
    by={x.key:x for x in criteria}; score=sum(x.points for x in criteria); cpp=by["cpp_depth"].status; app=by["application_logic"].status; arch=by["architecture"].status; egym=by["egym_fit"].status
    summary=str(payload.get("summary") or "Assessment based on professional Experience.")
    if payload.get("hard_skip") is True: decision="SKIP"; score=min(score,49); summary=str(payload.get("hard_skip_reason") or summary)
    elif cpp=="not_met": decision="SKIP"; score=min(score,39)
    elif cpp in ("partial","unknown"): decision="REVIEW"; score=min(score,64)
    elif app=="not_met": decision="SKIP"; score=min(score,49)
    elif app=="unknown" or egym=="unknown": decision="REVIEW"; score=min(score,69)
    elif app=="met" and egym=="met": decision="STRONG OUTREACH" if score>=85 else "OUTREACH"
    elif app=="met" and egym=="partial": decision="OUTREACH"
    elif app=="partial" and arch=="met" and egym in ("met","partial"): decision="OUTREACH"
    else: decision="REVIEW"
    return ScoreResult(score=score,decision=decision,summary=summary[:200],criteria=criteria)

@app.get("/health")
def health(): return {"ok":True,"model":OLLAMA_MODEL,"mode":"robust-structured","version":"2.2.1"}

@app.post("/analyze",response_model=ScoreResult)
def analyze(profile:Profile):
    experience=re.sub(r"\s+"," ",profile.visibleProfileText)[:10000]
    body={"model":OLLAMA_MODEL,"keep_alive":"60m","stream":False,"think":False,"format":MODEL_SCHEMA,"messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":"Evaluate every supplied job, duration, description and job-level Skills. Return all six criteria exactly once.\n\n"+experience}],"options":{"temperature":0,"num_ctx":3072,"num_predict":360}}
    try:
        with httpx.Client(timeout=90.0) as client:
            r=client.post(OLLAMA_URL,json=body); r.raise_for_status()
        raw=r.json().get("message",{}).get("content","")
        return normalize(json.loads(raw))
    except httpx.ConnectError as exc: raise HTTPException(503,"Ollama is not reachable.") from exc
    except httpx.HTTPStatusError as exc: raise HTTPException(502,f"Ollama error: {exc.response.text[:400]}") from exc
    except (json.JSONDecodeError,ValidationError) as exc: raise HTTPException(502,f"Could not read model response: {exc}") from exc
    except Exception as exc: raise HTTPException(500,f"Analysis failed: {exc}") from exc
