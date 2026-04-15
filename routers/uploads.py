"""Uploads: список/скачивание/удаление загруженных файлов + HTML-страница."""
import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from database import get_db
from helpers import UPLOADS_DIR

router = APIRouter(tags=["uploads"])


@router.get("/api/uploads")
def list_uploads(db: Session = Depends(get_db)):
    from database import UploadedFile
    files = db.query(UploadedFile).order_by(UploadedFile.uploaded_at.desc()).all()
    type_labels = {
        "kaspi_active":  "Kaspi ACTIVE",
        "kaspi_archive": "Kaspi ARCHIVE",
        "price_list":    "Прайс (накладные)",
        "pricelist_ref": "Справочник",
    }
    return [
        {
            "id": f.id,
            "original_name": f.original_name,
            "saved_name": f.saved_name,
            "file_type": f.file_type,
            "type_label": type_labels.get(f.file_type, f.file_type),
            "size_bytes": f.size_bytes,
            "records": f.records,
            "uploaded_at": f.uploaded_at.strftime("%d.%m.%Y %H:%M") if f.uploaded_at else "",
            "exists": os.path.exists(os.path.join(UPLOADS_DIR, f.saved_name)) if f.saved_name else False,
        }
        for f in files
    ]


@router.get("/api/uploads/{upload_id}/download")
def download_upload(upload_id: int, db: Session = Depends(get_db)):
    from database import UploadedFile
    f = db.query(UploadedFile).filter(UploadedFile.id == upload_id).first()
    if not f or not f.saved_name:
        raise HTTPException(status_code=404, detail="Файл не найден")
    path = os.path.join(UPLOADS_DIR, f.saved_name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Файл удалён с диска")
    return FileResponse(path, filename=f.original_name, media_type="application/octet-stream")


@router.delete("/api/uploads/{upload_id}")
def delete_upload(upload_id: int, db: Session = Depends(get_db)):
    from database import UploadedFile
    f = db.query(UploadedFile).filter(UploadedFile.id == upload_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Не найдено")
    if f.saved_name:
        path = os.path.join(UPLOADS_DIR, f.saved_name)
        if os.path.exists(path):
            os.remove(path)
    db.delete(f)
    db.commit()
    return {"ok": True}


@router.get("/uploads")
def uploads_page():
    return FileResponse("static/uploads.html")
