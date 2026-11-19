from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import current_user
from ..models import Notification, User
from ..schemas import NotificationList, NotificationOut

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


@router.get("", response_model=NotificationList)
def list_notifications(limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db),
                       user: User = Depends(current_user)):
    """My notifications (created by background jobs), newest first, plus the unread count."""
    items = db.scalars(select(Notification).where(Notification.user_id == user.id)
                       .order_by(Notification.id.desc()).limit(limit)).all()
    unread = db.scalar(select(func.count()).select_from(Notification)
                       .where(Notification.user_id == user.id, Notification.read.is_(False)))
    return {"unread": unread, "items": items}


@router.post("/read-all", status_code=204)
def mark_all_read(db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.execute(update(Notification).where(Notification.user_id == user.id).values(read=True))
    db.commit()


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    note = db.get(Notification, notification_id)
    if note is None or note.user_id != user.id:  # 404, not 403: don't reveal other users' ids
        raise HTTPException(404, "Notification not found")
    note.read = True
    db.commit()
    return note
