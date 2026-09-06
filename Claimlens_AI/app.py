from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.claim_analyzer import ClaimAnalyzer, build_demo_claims
from src.config import APP_HOST, APP_PORT, BASE_DIR, STATIC_DIR, UPLOADS_DIR
from src.database import (
    AuditEvent,
    Claim,
    Document,
    ExtractedFact,
    Finding,
    HumanDecision,
    SessionLocal,
    User,
    create_claim,
    get_claim_by_id,
    get_dashboard_stats,
    initialize_database,
    list_claims,
    record_human_decision,
)
from src.document_parser import DocumentParser
from src.models import (
    ClaimCreateRequest,
    InvestigatorDecisionRequest,
    ScenarioSimulateRequest,
    UserCreate,
    UserLogin,
)

app = FastAPI(title="ClaimLens_AI - Insurance Claims Evidence Intelligence")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = STATIC_DIR
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

analyzer = ClaimAnalyzer()


@app.on_event("startup")
def startup_event() -> None:
    initialize_database()
    build_demo_claims()
    # Run initial baseline analysis on all 5 demo claims
    for claim in list_claims():
        if claim["claim_id"].startswith("CLM-2026-00"):
            try:
                analyzer.analyze_claim(claim["claim_id"])
            except Exception as e:
                print(f"Error analyzing {claim['claim_id']}: {e}")


# ==========================================
# HTML PAGE ROUTES
# ==========================================

@app.get("/", response_class=HTMLResponse)
def serve_home() -> FileResponse:
    return FileResponse(frontend_dir / "index.html")


@app.get("/dashboard", response_class=HTMLResponse)
def serve_dashboard() -> FileResponse:
    return FileResponse(frontend_dir / "dashboard.html")


@app.get("/claims", response_class=HTMLResponse)
def serve_claims_list() -> FileResponse:
    return FileResponse(frontend_dir / "dashboard.html")


@app.get("/new-claim", response_class=HTMLResponse)
def serve_new_claim() -> FileResponse:
    return FileResponse(frontend_dir / "new_claim.html")


@app.get("/evidence-review/{claim_id}", response_class=HTMLResponse)
def serve_evidence_review(claim_id: str) -> FileResponse:
    return FileResponse(frontend_dir / "evidence_review.html")


@app.get("/policy-library", response_class=HTMLResponse)
def serve_policy_library() -> FileResponse:
    return FileResponse(frontend_dir / "policy_library.html")


@app.get("/login", response_class=HTMLResponse)
def serve_login() -> FileResponse:
    return FileResponse(frontend_dir / "login.html")


@app.get("/signup", response_class=HTMLResponse)
def serve_signup() -> FileResponse:
    return FileResponse(frontend_dir / "signup.html")


@app.get("/profile", response_class=HTMLResponse)
def serve_profile() -> FileResponse:
    return FileResponse(frontend_dir / "profile.html")


# ==========================================
# REST API ROUTES
# ==========================================

@app.get("/api/dashboard-stats")
def get_stats() -> JSONResponse:
    return JSONResponse(content={"status": "success", "stats": get_dashboard_stats()})


@app.get("/api/claims")
def get_claims_list(status: Optional[str] = None, risk_level: Optional[str] = None) -> JSONResponse:
    claims = list_claims(status=status, risk_level=risk_level)
    return JSONResponse(content={"status": "success", "claims": claims})


@app.post("/api/claims")
def create_new_claim(payload: ClaimCreateRequest) -> JSONResponse:
    data = payload.model_dump()
    created = create_claim(data)
    return JSONResponse(content={"status": "success", "claim": created})


@app.get("/api/claims/{claim_id}")
def get_single_claim(claim_id: str) -> JSONResponse:
    claim = get_claim_by_id(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    return JSONResponse(content={"status": "success", "claim": claim})


@app.post("/api/claims/{claim_id}/upload")
async def upload_claim_documents(claim_id: str, files: List[UploadFile] = File(...)) -> JSONResponse:
    claim = get_claim_by_id(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    claim_dir = UPLOADS_DIR / claim_id
    claim_dir.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    uploaded_records = []
    try:
        for file in files:
            safe_filename = file.filename or f"doc_{int(datetime.utcnow().timestamp())}.txt"
            save_path = claim_dir / safe_filename

            # Actually write file content to disk
            contents = await file.read()
            with open(save_path, "wb") as f:
                f.write(contents)

            # Parse document contents
            pages = DocumentParser.extract_text_from_file(save_path)
            doc_text = "\n".join(p["text"] for p in pages)
            extracted_facts = DocumentParser.extract_structured_facts(pages, safe_filename)

            doc_record = Document(
                claim_id=claim_id,
                file_name=safe_filename,
                file_type=save_path.suffix.replace(".", "").upper(),
                file_path=str(save_path),
                file_size=len(contents),
                extracted_text=doc_text,
                extraction_status="PARSED",
            )
            db.add(doc_record)
            db.flush()

            # Save extracted facts
            for ef in extracted_facts:
                db_fact = ExtractedFact(
                    claim_id=claim_id,
                    document_id=doc_record.id,
                    document_name=safe_filename,
                    field_name=ef["field_name"],
                    field_value=ef["field_value"],
                    source_page=ef.get("source_page", "Page 1"),
                    confidence=ef.get("confidence", "HIGH"),
                    raw_snippet=ef.get("raw_snippet", ""),
                )
                db.add(db_fact)

            db.add(AuditEvent(
                claim_id=claim_id,
                event_type="Document Uploaded",
                actor="Investigator",
                description=f"Uploaded & parsed '{safe_filename}' ({len(contents)} bytes, {len(extracted_facts)} facts extracted)",
            ))

            uploaded_records.append({
                "file_name": safe_filename,
                "file_size": len(contents),
                "facts_extracted": len(extracted_facts),
            })

        db.commit()
    finally:
        db.close()

    # Automatically re-run claim intelligence
    analysis_result = analyzer.analyze_claim(claim_id)
    return JSONResponse(content={
        "status": "success",
        "uploaded": uploaded_records,
        "claim": analysis_result,
    })


@app.post("/api/claims/{claim_id}/analyze")
def trigger_analysis(claim_id: str) -> JSONResponse:
    claim = get_claim_by_id(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    result = analyzer.analyze_claim(claim_id)
    return JSONResponse(content={"status": "success", "claim": result})


@app.post("/api/claims/{claim_id}/decision")
def submit_investigator_decision(claim_id: str, payload: InvestigatorDecisionRequest) -> JSONResponse:
    claim = get_claim_by_id(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    updated_claim = record_human_decision(
        claim_id=claim_id,
        decision=payload.decision,
        reason=payload.reason or "",
        notes=payload.notes or "",
        investigator=payload.investigator_name or "Lead Investigator",
    )
    return JSONResponse(content={"status": "success", "claim": updated_claim})


@app.post("/api/claims/{claim_id}/simulate")
def simulate_scenario_endpoint(claim_id: str, payload: ScenarioSimulateRequest) -> JSONResponse:
    claim = get_claim_by_id(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    sim_result = analyzer.simulate_scenario(claim_id, payload.modified_facts)
    return JSONResponse(content={"status": "success", "simulation": sim_result})


@app.get("/api/policy-clauses")
def search_policy_clauses(query: Optional[str] = None) -> JSONResponse:
    q = query or "accidental collision damage claim evidence policy"
    matches = analyzer.policy_retriever.retrieve_relevant_clauses(q, top_k=8)
    clauses = [
        {
            **m["clause"],
            "similarity": m["similarity"],
            "relevance": m["relevance"],
            "why_matched": m["why_matched"],
        }
        for m in matches
    ]
    return JSONResponse(content={"status": "success", "clauses": clauses, "retrieval_method": "TF-IDF Normalized Vector Cosine Retrieval"})


@app.post("/api/auth/login")
def login(payload: UserLogin) -> JSONResponse:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == payload.username).first()
        if not user or user.password_hash != f"pbkdf2:sha256:claimlens2026":
            # For hackathon convenience, accept demo investigator login
            if payload.username == "investigator":
                pass
            else:
                raise HTTPException(status_code=401, detail="Invalid username or credentials")

        return JSONResponse(content={
            "status": "success",
            "user": {
                "username": user.username if user else "investigator",
                "full_name": user.full_name if user else "Sarah Jenkins",
                "email": user.email if user else "investigator@claimlens.ai",
                "role": user.role if user else "Senior Claims Investigator",
            }
        })
    finally:
        db.close()


@app.post("/api/auth/signup")
def signup(payload: UserCreate) -> JSONResponse:
    db = SessionLocal()
    try:
        if db.query(User).filter(User.username == payload.username).first():
            raise HTTPException(status_code=400, detail="Username already exists")

        user = User(
            username=payload.username,
            email=payload.email,
            password_hash=f"pbkdf2:sha256:claimlens2026",
            full_name=payload.full_name,
            role=payload.role or "Investigator",
        )
        db.add(user)
        db.commit()
        return JSONResponse(content={
            "status": "success",
            "user": {
                "username": user.username,
                "full_name": user.full_name,
                "email": user.email,
                "role": user.role,
            }
        })
    finally:
        db.close()


@app.get("/api/auth/me")
def get_current_user() -> JSONResponse:
    return JSONResponse(content={
        "status": "success",
        "user": {
            "username": "investigator",
            "full_name": "Sarah Jenkins",
            "email": "investigator@claimlens.ai",
            "role": "Senior Claims Investigator",
        }
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=APP_HOST, port=APP_PORT, reload=False)
