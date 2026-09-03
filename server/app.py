import json
import re
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

app = FastAPI(title="Alex Candidate Scorer", version="2.2.0")
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
    {"key":"cpp_depth","label":"C++ professional experience","max_points":40},
    {"key":"application_logic","label":"Application / business logic","max_points":25},
    {"key":"architecture","label":"Architecture / software design","max_points":10},
    {"key":"egym_fit","label":"EGYM product-software fit","max_points":15},
    {"key":"qt_qml","label":"Qt / QML","max_points":5},
    {"key":"cmake","label":"CMake","max_points":5},
]
MULT={"met":1.0,"partial":0.5,"not_met":0.0,"unknown":0.0}

SYSTEM_PROMPT="""You assess LinkedIn PROFESSIONAL EXPERIENCE for an EGYM Senior C++ Software Engineer. Read the complete supplied Experience carefully and use explicit evidence only.

IMPORTANT LINKEDIN FORMAT
Each Experience entry can contain title, employer, employment type, dates/duration, description, then a 'Skills:' line. A Skills line INSIDE that job entry is evidence for technologies used in THAT JOB. It is NOT the global LinkedIn Skills section.
Example: Full-time Software Engineer, 4 yrs 9 mos, description contains '(Qt C++, cmake)' = explicit professional C++/Qt/CMake experience in that role.
Example: Full-time Software Developer, 2 yrs 3 mos, Skills: ... C++ ... = 2 yrs 3 mos professional C++ evidence.
Do not require C++ to appear in the job title.

C++ PROFESSIONAL EXPERIENCE
Need at least 3 total years of regular professional C++ for MET. Sum qualifying durations across jobs where C++ is explicitly tied to the job via description OR that job's Skills line.
Intern, internship, Werkstudent/working student, student jobs, thesis and university projects count ZERO.
MET = >=3 years. PARTIAL = >0 but <3 years OR duration cannot establish 3 years. NOT_MET = no qualifying professional C++ evidence. UNKNOWN only if evidence cannot be interpreted.

APPLICATION / BUSINESS LOGIC
Judge WHAT the candidate built. Strong evidence includes applications, product features, domain/business logic, workflows, UI/HMI/desktop software, backend/services, data processing, complex application modules, product ownership and substantial application maintenance. Do not require the literal words 'business logic'.
MET = clearly substantial application/product development. PARTIAL = mixed or limited. NOT_MET = clearly low-level-only. UNKNOWN = insufficient detail.

ARCHITECTURE / SOFTWARE DESIGN
Look for architecture/design ownership, modules/components/interfaces, design patterns, large refactoring, legacy modernization, technical design, maintainability. MET strong explicit evidence; PARTIAL some evidence; UNKNOWN if not described. Do not mark NOT_MET merely because architecture is not mentioned.

EGYM PRODUCT-SOFTWARE FIT
EGYM builds a large, long-lived C++ software product running fitness machines, with many features/components, application/domain logic, UI, hardware interaction, testing and maintainability. Estimate technical transferability, NOT personality/culture fit.
Strong: complex C++ product/application software; Qt/HMI/GUI; application software interacting with machines/devices; medical/industrial/robotics/instrumentation; large product codebases; cross-functional hardware+software; complex domain logic.
A candidate can also fit without hardware if they have strong complex C++ application/product experience.
'Large scale' means complex/long-lived product/codebase, NOT web traffic.
MET strong transferability; PARTIAL reasonable but incomplete/unclear; NOT_MET clearly unrelated/low-level-only; UNKNOWN insufficient detail.

LOW-LEVEL HARD SKIP
Hard skip only when the professional profile is predominantly ECU/AUTOSAR/firmware/microcontroller/BSP/driver/device-controller/register-level work AND lacks substantial application/business-logic development. Embedded/hardware experience itself is NOT a skip.

NICE TO HAVE
Qt/QML and CMake are only 5 points each. Missing them never rejects a strong core candidate.

DECISION PRINCIPLES
<3 years professional C++ cannot be OUTREACH. >=3 years + strong application/product development can be OUTREACH. Strong EGYM transferability/architecture strengthens the result. Unclear core evidence => REVIEW. Predominantly low-level-only => SKIP.

Return JSON only, with short factual evidence quoting/paraphrasing the relevant job evidence:
{"summary":"under 20 words","hard_skip":false,"hard_skip_reason":"","criteria":[{"key":"cpp_depth","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"application_logic","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"architecture","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"egym_fit","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"qt_qml","status":"met|partial|not_met|unknown","evidence":"..."},{"key":"cmake","status":"met|partial|not_met|unknown","evidence":"..."}]}
"""

def clean_json(text):
    text=re.sub(r"^```(?:json)?\s*","",text.strip(),flags=re.I)
    text=re.sub(r"\s*```$","",text)
    a,b=text.find("{"),text.rfind("}")
    if a<0 or b<=a: raise ValueError("Model did not return JSON")
    return text[a:b+1]

def normalize(payload):
    incoming={x.get("key"):x for x in payload.get("criteria",[])}
    criteria=[]
    for d in CRITERIA:
        raw=incoming.get(d["key"],{})
        status=raw.get("status","unknown")
        if status not in MULT: status="unknown"
        evidence=str(raw.get("evidence","")).strip() or "Not visible in Experience."
        criteria.append(Criterion(key=d["key"],label=d["label"],status=status,points=round(d["max_points"]*MULT[status]),max_points=d["max_points"],evidence=evidence[:220]))
    by={x.key:x for x in criteria}; score=sum(x.points for x in criteria)
    cpp,app,arch,egym=by["cpp_depth"].status,by["application_logic"].status,by["architecture"].status,by["egym_fit"].status
    if payload.get("hard_skip") is True:
        decision="SKIP"; score=min(score,49); summary=str(payload.get("hard_skip_reason") or "Predominantly low-level profile.")
    elif cpp=="not_met": decision="SKIP"; score=min(score,39); summary=str(payload.get("summary") or "No qualifying professional C++.")
    elif cpp in ("partial","unknown"): decision="REVIEW"; score=min(score,64); summary=str(payload.get("summary") or "Professional C++ duration below 3 years or unclear.")
    elif app=="not_met": decision="SKIP"; score=min(score,49); summary=str(payload.get("summary") or "Application development missing.")
    elif app=="unknown" or egym=="unknown": decision="REVIEW"; score=min(score,69); summary=str(payload.get("summary") or "Core product fit unclear.")
    elif app=="met" and egym=="met": decision="STRONG OUTREACH" if score>=85 else "OUTREACH"; summary=str(payload.get("summary") or "Strong EGYM-transferable C++ application background.")
    elif app=="met" and egym in ("partial","met"): decision="OUTREACH"; summary=str(payload.get("summary") or "Good C++ application background.")
    elif app=="partial" and arch=="met" and egym in ("met","partial"): decision="OUTREACH"; summary=str(payload.get("summary") or "Transferable C++ product background.")
    else: decision="REVIEW"; summary=str(payload.get("summary") or "Needs review.")
    return ScoreResult(score=score,decision=decision,summary=summary[:200],criteria=criteria)

@app.get("/health")
def health(): return {"ok":True,"model":OLLAMA_MODEL,"mode":"robust-semantic","version":"2.2.0"}

@app.post("/analyze",response_model=ScoreResult)
def analyze(profile:Profile):
    # Reliability first: let the model see the full extracted Experience, including per-job Skills.
    experience=re.sub(r"\s+"," ",profile.visibleProfileText)[:10000]
    body={"model":OLLAMA_MODEL,"keep_alive":"60m","stream":False,"think":False,"format":"json","messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":"Evaluate this extracted LinkedIn Experience. Read every job, its duration, description and job-level Skills.\n\n"+experience}],"options":{"temperature":0,"num_ctx":3072,"num_predict":300}}
    try:
        with httpx.Client(timeout=90.0) as client:
            r=client.post(OLLAMA_URL,json=body); r.raise_for_status()
        raw=r.json().get("message",{}).get("content","")
        return normalize(json.loads(clean_json(raw)))
    except httpx.ConnectError as exc: raise HTTPException(503,"Ollama is not reachable.") from exc
    except httpx.HTTPStatusError as exc: raise HTTPException(502,f"Ollama error: {exc.response.text[:400]}") from exc
    except (json.JSONDecodeError,ValueError,ValidationError) as exc: raise HTTPException(502,f"Could not read model response: {exc}") from exc
    except Exception as exc: raise HTTPException(500,f"Analysis failed: {exc}") from exc
