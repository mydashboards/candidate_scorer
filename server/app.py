import json
import re
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title="Alex Candidate Scorer", version="2.1.0")
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
    score: int
    decision: Literal["STRONG OUTREACH", "OUTREACH", "REVIEW", "SKIP"]
    summary: str
    criteria: list[Criterion]

LOW = ("autosar", " ecu", "ecu ", "firmware", "microcontroller", "device controller", "bsp", "kernel driver", "device driver", "register-level", "hardware abstraction", "can bus", "lin bus")
APP = ("application", "business logic", "domain logic", "product software", "desktop", "workflow", "feature development", "user interface", "gui", "hmi", "backend", "api", "service")
ARCH = ("architecture", "architectural", "design pattern", "domain-driven", "ddd", "refactor", "legacy", "component design", "module design", "software design")
EGYM = ("medical device", "robotics", "robotic", "industrial", "machine", "instrumentation", "device software", "product software", "large codebase", "complex software", "production software")

def hits(text, terms):
    low = text.lower()
    return [x for x in terms if x in low]

def keyword_status(found, strong=2):
    return "met" if len(found) >= strong else "partial" if found else "unknown"

def criterion(key, label, status, max_points, evidence):
    mult = {"met":1,"partial":.5,"not_met":0,"unknown":0}[status]
    return Criterion(key=key,label=label,status=status,points=round(max_points*mult),max_points=max_points,evidence=evidence[:180])

def fast_signals(text):
    t=text.lower()
    return {"cpp":hits(t,("c++","modern c++","cpp")),"low":hits(t,LOW),"app":hits(t,APP),"arch":hits(t,ARCH),"egym":hits(t,EGYM),"qt":hits(t,("qt","qml")),"cmake":hits(t,("cmake",))}

AI_PROMPT="""Assess professional EXPERIENCE for an EGYM C++ engineer.
CRITICAL LINKEDIN RULE: a 'Skills:' line shown directly inside a specific job entry belongs to THAT JOB. If C++ appears in that job's Skills line, treat C++ as explicitly used in that professional role for the full displayed duration of that role, unless the role is Intern/Internship/Working Student/Werkstudent/student/thesis. Do not require the prose description to repeat C++.
Example: 'Software Developer | Full-time | Jul 2024-Present | 2 yrs 3 mos | Skills: ... C++ ...' = 2 years 3 months qualifying professional C++ evidence.
Sum qualifying durations across regular professional jobs that explicitly list C++ in their job description OR job-level Skills line. Need >=3 years total for c=M. If >0 but <3 years, c=P, NEVER c=N. c=N only when there is no qualifying professional C++ evidence.
EGYM software is a large long-lived C++ product on fitness machines with application/business/domain logic, many features/components and architecture. Hardware-adjacent application/product software is good; firmware/ECU/controller-only is poor. Large-scale means complex/long-lived codebase, not web traffic.
Return JSON only: {"c":"M|P|U|N","a":"M|P|U|N","r":"M|P|U|N","e":"M|P|U|N","l":false,"s":"max 12 words"}
c=C++ duration; a=application/business logic; r=architecture; e=EGYM transferability; l=true only predominantly low-level without strong application layer.
"""
MAP={"M":"met","P":"partial","U":"unknown","N":"not_met"}

def ai_assess(text):
    compact=re.sub(r"\s+"," ",text)[:7500]
    body={"model":OLLAMA_MODEL,"keep_alive":"60m","stream":False,"think":False,"format":"json","messages":[{"role":"system","content":AI_PROMPT},{"role":"user","content":compact}],"options":{"temperature":0,"num_ctx":2048,"num_predict":100}}
    with httpx.Client(timeout=30.0) as client:
        r=client.post(OLLAMA_URL,json=body); r.raise_for_status()
    raw=r.json().get("message",{}).get("content","").strip()
    start,end=raw.find("{"),raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Model returned empty/non-JSON response")
    return json.loads(raw[start:end+1])

def build_result(ai,sig):
    def st(k): return MAP.get(str(ai.get(k,"U")).upper(),"unknown")
    cpp,app,arch,egym=st("c"),st("a"),st("r"),st("e")
    qt="met" if sig["qt"] else "unknown"; cmake="met" if sig["cmake"] else "unknown"
    cpp_ev = "C++ found in professional Experience; AI evaluates linked job durations." if sig["cpp"] else "No C++ visible in Experience."
    criteria=[criterion("cpp_depth","C++ professional experience",cpp,40,cpp_ev),criterion("application_logic","Application / business logic",app,25,", ".join(sig["app"][:4]) or "Semantic assessment of job descriptions."),criterion("architecture","Architecture / software design",arch,10,", ".join(sig["arch"][:4]) or "Semantic assessment of job descriptions."),criterion("egym_fit","EGYM product-software fit",egym,15,", ".join(sig["egym"][:4]) or "Transferability assessed from job descriptions."),criterion("qt_qml","Qt / QML",qt,5,", ".join(sig["qt"]) or "Not visible."),criterion("cmake","CMake",cmake,5,", ".join(sig["cmake"]) or "Not visible.")]
    score=sum(x.points for x in criteria)
    summary=str(ai.get("s") or "Transferability assessed from professional experience.")
    if ai.get("l") is True: return ScoreResult(score=min(score,49),decision="SKIP",summary=summary,criteria=criteria)
    if cpp=="not_met": return ScoreResult(score=min(score,39),decision="SKIP",summary=summary,criteria=criteria)
    if cpp in ("partial","unknown"): return ScoreResult(score=min(score,64),decision="REVIEW",summary=summary,criteria=criteria)
    if app=="not_met": return ScoreResult(score=min(score,49),decision="SKIP",summary=summary,criteria=criteria)
    if app=="unknown" or egym=="unknown": return ScoreResult(score=min(score,69),decision="REVIEW",summary=summary,criteria=criteria)
    if app=="met" and egym=="met": decision="STRONG OUTREACH" if score>=85 else "OUTREACH"
    elif app=="met" and egym=="partial": decision="OUTREACH"
    elif app=="partial" and arch=="met" and egym in ("met","partial"): decision="OUTREACH"
    else: decision="REVIEW"
    return ScoreResult(score=score,decision=decision,summary=summary,criteria=criteria)

@app.get("/health")
def health(): return {"ok":True,"model":OLLAMA_MODEL,"mode":"fast-hybrid","version":"2.1.0"}

@app.post("/analyze",response_model=ScoreResult)
def analyze(profile:Profile):
    sig=fast_signals(profile.visibleProfileText)
    if not sig["cpp"]:
        criteria=[criterion("cpp_depth","C++ professional experience","not_met",40,"No C++ evidence in extracted Experience."),criterion("application_logic","Application / business logic",keyword_status(sig["app"]),25,", ".join(sig["app"][:4]) or "Not visible."),criterion("architecture","Architecture / software design",keyword_status(sig["arch"]),10,", ".join(sig["arch"][:4]) or "Not visible."),criterion("egym_fit","EGYM product-software fit",keyword_status(sig["egym"]),15,", ".join(sig["egym"][:4]) or "Not visible."),criterion("qt_qml","Qt / QML","met" if sig["qt"] else "unknown",5,", ".join(sig["qt"]) or "Not visible."),criterion("cmake","CMake","met" if sig["cmake"] else "unknown",5,", ".join(sig["cmake"]) or "Not visible.")]
        return ScoreResult(score=min(sum(x.points for x in criteria),39),decision="SKIP",summary="No professional C++ evidence found.",criteria=criteria)
    try: return build_result(ai_assess(profile.visibleProfileText),sig)
    except httpx.ConnectError as exc: raise HTTPException(503,"Ollama is not reachable.") from exc
    except Exception as exc: raise HTTPException(502,f"Fast analysis failed: {exc}") from exc
