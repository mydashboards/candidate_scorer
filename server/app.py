import re
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title="Alex Candidate Scorer", version="2.0-fast-basic")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

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


def hits(text: str, patterns: list[str]) -> list[str]:
    found = []
    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            found.append(pattern)
    return found


def clean_label(pattern: str) -> str:
    return (
        pattern.replace(r"\b", "")
        .replace(r"\+\+", "++")
        .replace("(?:", "(")
        .replace("|", "/")
        .replace("\\", "")
    )


@app.get("/health")
def health():
    return {"ok": True, "mode": "fast-basic", "version": "2.0-fast-basic", "model": "none"}


@app.post("/analyze", response_model=ScoreResult)
def analyze(profile: Profile):
    text = profile.visibleProfileText
    lower = text.lower()

    cpp_patterns = [r"\bc\+\+\b", r"modern c\+\+", r"cpp\b"]
    cpp_matches = list(re.finditer(r"\bc\+\+\b|modern c\+\+|\bcpp\b", text, flags=re.IGNORECASE))
    cpp_count = len(cpp_matches)

    app_patterns = [
        r"application development", r"application software", r"desktop", r"gui\b",
        r"user interface", r"business logic", r"domain logic", r"software architecture",
        r"architect(?:ure|ural)?", r"feature development", r"product software",
        r"design patterns?", r"\bddd\b", r"qt quick", r"qml\b", r"hmi\b",
        r"visualization", r"backend", r"frontend", r"medical software",
        r"healthcare", r"kiosk", r"multi-process", r"ipc\b", r"protobuf"
    ]
    app_found = hits(text, app_patterns)

    qt_found = hits(text, [r"\bqt\b", r"qt quick", r"\bqml\b"])
    cmake_found = hits(text, [r"\bcmake\b"])

    low_level_patterns = [
        r"\bautosar\b", r"\becu\b", r"microcontroller", r"\bmcu\b",
        r"\bbsp\b", r"device driver", r"kernel driver", r"bare.?metal",
        r"firmware", r"bootloader", r"low-level hardware"
    ]
    low_found = hits(text, low_level_patterns)

    student_patterns = [r"intern\b", r"internship", r"werkstudent", r"working student", r"thesis", r"university project", r"student research"]
    student_found = hits(text, student_patterns)

    # C++ is intentionally simple: recurring C++ signals across the loaded Experience = strong.
    # One isolated mention = partial and asks Alex to verify duration manually.
    if cpp_count >= 2:
        cpp_status, cpp_points = "met", 50
        cpp_evidence = f"C++ appears repeatedly in Experience ({cpp_count} signals). Verify years visually."
    elif cpp_count == 1:
        cpp_status, cpp_points = "partial", 25
        cpp_evidence = "One explicit C++ signal found. Check professional duration manually."
    else:
        cpp_status, cpp_points = "not_met", 0
        cpp_evidence = "No explicit C++ signal found in loaded Experience."

    if len(app_found) >= 2:
        app_status, app_points = "met", 40
        examples = ", ".join(clean_label(x) for x in app_found[:3])
        app_evidence = f"Strong application/product signals: {examples}."
    elif len(app_found) == 1:
        app_status, app_points = "partial", 20
        app_evidence = f"Some application signal: {clean_label(app_found[0])}."
    else:
        app_status, app_points = "unknown", 0
        app_evidence = "No clear application/business-logic signal found."

    if qt_found:
        qt_status, qt_points = "met", 5
        qt_evidence = "Qt/QML explicitly found."
    else:
        qt_status, qt_points = "unknown", 0
        qt_evidence = "Qt/QML not visible."

    if cmake_found:
        cmake_status, cmake_points = "met", 5
        cmake_evidence = "CMake explicitly found."
    else:
        cmake_status, cmake_points = "unknown", 0
        cmake_evidence = "CMake not visible."

    score = cpp_points + app_points + qt_points + cmake_points

    # Hard skip only when low-level signals dominate and there is no application evidence.
    hard_low_level = len(low_found) >= 2 and app_status in ("unknown", "not_met")

    if hard_low_level:
        decision = "SKIP"
        score = min(score, 39)
        summary = "Mostly low-level/firmware signals without clear application-layer evidence."
    elif cpp_status == "met" and app_status == "met" and score >= 95:
        decision = "STRONG OUTREACH"
        summary = "Strong C++ + application fit; Qt/CMake strengthen the match."
    elif cpp_status == "met" and app_status == "met":
        decision = "OUTREACH"
        summary = "Strong C++ and application/product signals. Worth outreach."
    elif cpp_status == "partial" and app_status == "met":
        decision = "REVIEW"
        summary = "Relevant profile, but verify C++ duration before outreach."
    elif cpp_status == "met":
        decision = "REVIEW"
        summary = "C++ looks relevant; verify application/business-logic depth."
    else:
        decision = "SKIP" if cpp_status == "not_met" else "REVIEW"
        summary = "Not enough C++ evidence for a confident outreach recommendation."

    if student_found and cpp_status == "partial":
        cpp_evidence += " Student/intern signals are not counted as professional depth."

    criteria = [
        Criterion(key="cpp_depth", label="C++ signal", status=cpp_status, points=cpp_points, max_points=50, evidence=cpp_evidence),
        Criterion(key="application_layer", label="Application / business logic", status=app_status, points=app_points, max_points=40, evidence=app_evidence),
        Criterion(key="qt_qml", label="Qt / QML", status=qt_status, points=qt_points, max_points=5, evidence=qt_evidence),
        Criterion(key="cmake", label="CMake", status=cmake_status, points=cmake_points, max_points=5, evidence=cmake_evidence),
    ]

    return ScoreResult(score=score, decision=decision, summary=summary, criteria=criteria)
