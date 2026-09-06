from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.claim_analyzer import ClaimAnalyzer, build_demo_claims
from src.config import APP_HOST, APP_PORT, BASE_DIR, DATA_DIR, POLICY_PATH
from src.database import create_claim, get_claim_by_id, get_dashboard_stats, initialize_database, list_claims, seed_demo_claims, update_claim
from src.models import ClaimRequest, InvestigatorDecision

app = FastAPI(title="ClaimLens_AI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = BASE_DIR / "frontend"
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

analyzer = ClaimAnalyzer()


@app.on_event("startup")
def startup_event() -> None:
    initialize_database()
    seed_demo_claims()
    for claim in list_claims():
        if claim["claim_id"].startswith("CLM-2026-00"):
            analyzer.analyze_claim(claim["claim_id"], claim)


@app.get("/", response_class=HTMLResponse)
def serve_dashboard() -> FileResponse:
    return FileResponse(frontend_dir / "index.html")


@app.get("/new-claim", response_class=HTMLResponse)
def serve_new_claim() -> FileResponse:
    return FileResponse(frontend_dir / "new_claim.html")


@app.get("/evidence-review/{claim_id}", response_class=HTMLResponse)
def serve_evidence_review(claim_id: str) -> FileResponse:
    return FileResponse(frontend_dir / "evidence_review.html")


@app.get("/report/{claim_id}", response_class=HTMLResponse)
def serve_report(claim_id: str) -> FileResponse:
    return FileResponse(frontend_dir / "report.html")


@app.get("/policy-library", response_class=HTMLResponse)
def serve_policy_library() -> FileResponse:
    return FileResponse(frontend_dir / "policy_library.html")


@app.get("/claims", response_class=HTMLResponse)
def serve_claims_list() -> FileResponse:
    return FileResponse(frontend_dir / "claims_list.html")


@app.get("/settings", response_class=HTMLResponse)
def serve_settings() -> FileResponse:
    return FileResponse(frontend_dir / "settings.html")


@app.post("/api/claims")
def create_or_update_claim(claim_data: ClaimRequest) -> JSONResponse:
    payload = claim_data.model_dump()
    created = create_claim(payload)
    documents = payload.get("documents", {})
    structured = {
        **created,
        "claim_form": {
            "incident_date": created["incident_date"],
            "vehicle_number": created["vehicle_registration"],
        },
        "fir": {} if not documents.get("fir_or_estimate") else {
            "incident_date": created["incident_date"],
            "vehicle_number": created["vehicle_registration"],
        },
        "repair_estimate": {
            "date": created["incident_date"],
            "vehicle_number": created["vehicle_registration"],
            "amount": created["claim_amount"],
        },
        "incident_description": {
            "incident_date": created["incident_date"],
            "vehicle_number": created["vehicle_registration"],
        },
    }
    result = analyzer.analyze_claim(created["claim_id"], structured)
    created = get_claim_by_id(created["claim_id"])
    return JSONResponse(content={"status": "success", "claim": created})


@app.post("/api/claims/{claim_id}/analyze")
async def analyze_claim(claim_id: str, files: List[UploadFile] = File(default=[])) -> JSONResponse:
    claim = get_claim_by_id(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    uploaded: Dict[str, Any] = {}
    for file in files:
        file_name = file.filename or "upload"
        safe_name = file_name.lower().replace(" ", "_")
        if safe_name:
            uploaded[safe_name] = {"filename": file_name, "content_type": file.content_type}

    claim_data = {**claim, **claim.get("analysis", {})}
    if uploaded:
        claim_data["documents"] = uploaded
    result = analyzer.analyze_claim(claim_id, claim_data)
    return JSONResponse(content={"status": "success", "result": result})


@app.get("/api/claims/{claim_id}")
def get_claim(claim_id: str) -> JSONResponse:
    claim = get_claim_by_id(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    return JSONResponse(content={"claim": claim})


@app.get("/api/claims")
def list_claims_api(status: Optional[str] = None, risk_level: Optional[str] = None) -> JSONResponse:
    claims = list_claims(status=status, risk_level=risk_level)
    return JSONResponse(content={"claims": claims})


@app.post("/api/claims/{claim_id}/decision")
def submit_decision(claim_id: str, decision: InvestigatorDecision) -> JSONResponse:
    claim = get_claim_by_id(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    analysis = {**(claim.get("analysis") or {}), "final_decision": decision.model_dump()}
    audit_trail = list(analysis.get("audit_trail") or [])
    audit_trail.append({"event": "Investigator decision recorded", "actor": "Investigator", "status": decision.decision, "notes": decision.notes or ""})
    analysis["audit_trail"] = audit_trail
    result = update_claim(claim_id, status=decision.decision, analysis=analysis)
    return JSONResponse(content={"status": "success", "claim": result})


@app.get("/api/policy-clauses")
def get_policy_clauses(search: Optional[str] = None) -> JSONResponse:
    query = search or "motor insurance claim evidence policy"
    matches = analyzer.policy_retriever.retrieve_relevant_clauses(query, top_k=20)
    clauses = [{**match["clause"], **{key: match[key] for key in ("similarity", "relevance", "matched_terms", "why_matched")}} for match in matches]
    return JSONResponse(content={"clauses": clauses, "retrieval": "local embedding cosine similarity"})


@app.get("/api/dashboard-stats")
def get_stats() -> JSONResponse:
    return JSONResponse(content={"stats": get_dashboard_stats()})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host=APP_HOST, port=APP_PORT, reload=False)
