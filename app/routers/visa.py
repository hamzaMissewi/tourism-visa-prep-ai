import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload

from app.core.gemini_client import generate_visa_plan
from app.core.security import get_current_user
from app.db.database import get_db
from app.db.models import VisaDocument, VisaPlan
from app.schemas.visa import (
    DocumentOut,
    DocumentStatusUpdate,
    VisaPlanCreateRequest,
    VisaPlanDetail,
    VisaPlanSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/visa", tags=["Visa"])


def plan_detail(plan: VisaPlan) -> VisaPlanDetail:
    return VisaPlanDetail(
        id=plan.id,
        country=plan.country,
        nationality=plan.nationality,
        purpose=plan.purpose,
        travel_date=plan.travel_date,
        duration_of_stay=plan.duration_of_stay,
        background=plan.background,
        overview=plan.overview,
        roadmap=plan.roadmap,
        interview_tips=plan.interview_tips,
        created_at=plan.created_at,
        documents=[DocumentOut.model_validate(doc) for doc in plan.documents],
    )


def get_owned_plan(db: Session, plan_id: int, user_id: int) -> VisaPlan:
    plan = (
        db.query(VisaPlan)
        .options(selectinload(VisaPlan.documents))
        .filter(VisaPlan.id == plan_id, VisaPlan.user_id == user_id)
        .first()
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="Visa plan not found")
    return plan


@router.post("/plans", response_model=VisaPlanDetail, status_code=201)
def create_visa_plan(
    request: VisaPlanCreateRequest,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    try:
        generated = generate_visa_plan(request)
    except Exception:
        logger.exception("Visa plan generation failed")
        raise HTTPException(
            status_code=502, detail="AI service is unavailable. Try again."
        )

    plan = VisaPlan(
        user_id=user_id,
        country=request.country,
        nationality=request.nationality,
        purpose=request.purpose,
        travel_date=request.travel_date,
        duration_of_stay=request.duration_of_stay,
        background=request.background,
        overview=generated.overview,
        roadmap=[step.model_dump() for step in generated.roadmap],
        interview_tips=generated.interview_tips,
    )
    db.add(plan)
    db.flush()  # get plan.id before inserting the checklist

    db.add_all(
        [
            VisaDocument(
                plan_id=plan.id,
                position=index,
                category=item.category,
                title=item.title,
                description=item.description,
                is_required=item.is_required,
            )
            for index, item in enumerate(generated.documents)
        ]
    )
    db.commit()

    return plan_detail(plan)


@router.get("/plans", response_model=list[VisaPlanSummary])
def list_visa_plans(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    plans = (
        db.query(VisaPlan)
        .options(selectinload(VisaPlan.documents))
        .filter(VisaPlan.user_id == user_id)
        .order_by(VisaPlan.id.desc())
        .all()
    )
    return [
        VisaPlanSummary(
            id=plan.id,
            country=plan.country,
            nationality=plan.nationality,
            purpose=plan.purpose,
            created_at=plan.created_at,
            documents_total=len(plan.documents),
            documents_collected=sum(1 for doc in plan.documents if doc.collected),
        )
        for plan in plans
    ]


@router.get("/plans/{plan_id}", response_model=VisaPlanDetail)
def get_visa_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    return plan_detail(get_owned_plan(db, plan_id, user_id))


@router.patch("/plans/{plan_id}/documents/{document_id}", response_model=DocumentOut)
def update_document_status(
    plan_id: int,
    document_id: int,
    request: DocumentStatusUpdate,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    get_owned_plan(db, plan_id, user_id)
    document = (
        db.query(VisaDocument)
        .filter(VisaDocument.id == document_id, VisaDocument.plan_id == plan_id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    document.collected = request.collected
    db.commit()
    db.refresh(document)
    return document
