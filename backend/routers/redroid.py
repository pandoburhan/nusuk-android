"""
Redroid Docker Orchestration API Router (`/api/redroid`).
Protected by Bearer `require_auth` when mounted in `backend/main.py`.
"""

import asyncio
import logging
from typing import Optional, Set

from fastapi import APIRouter, Body, HTTPException

from backend.adb_manager import adb_manager
from backend.database import SessionLocal
from backend.event_log import log_event
from backend.models import Instance, LogSeverity
from backend.orchestrator import orchestrator
from backend.redroid_manager import (
    REDROID_IMAGE,
    RedroidNotSupportedError,
    redroid_manager,
)
from backend.schemas import ActionResponse, RedroidDeployRequest
from backend.telemetry import telemetry_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/redroid", tags=["Redroid"])

# Keep strong references to background asyncio tasks so GC never cancels them mid-flight
_background_tasks: Set[asyncio.Task] = set()


def _track_task(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _bg_pull_image() -> None:
    def _emit(event_data):
        telemetry_manager.emit_sync("redroid_provisioning_event", event_data)

    try:
        await asyncio.to_thread(redroid_manager.pull_image, _emit)
        log_event(LogSeverity.INFO, "REDROID", f"Docker image {REDROID_IMAGE} berhasil diunduh.")
    except Exception as e:
        logger.error(f"Background Redroid image pull failed: {e}")
        log_event(LogSeverity.ERROR, "REDROID", f"Gagal mengunduh image {REDROID_IMAGE}: {e}")


async def _bg_deploy_and_provision(
    display_name: Optional[str] = None,
    adb_port: Optional[int] = None,
) -> None:
    telemetry_manager.emit_sync(
        "redroid_provisioning_event",
        {
            "stage": "deploying",
            "status": "progress",
            "title": "Redroid Deploy",
            "message": "Starting new Redroid Android 9 container...",
        },
    )

    deployed = None
    try:
        deployed = await asyncio.to_thread(
            redroid_manager.deploy_redroid_instance,
            None,
            adb_port,
            display_name,
        )
        log_event(
            LogSeverity.INFO,
            "REDROID",
            f"Container '{deployed['container_name']}' berjalan di 127.0.0.1:{deployed['adb_port']}. Memulai auto-provisioning...",
        )
        await adb_manager.provision_redroid_instance(
            adb_port=deployed["adb_port"],
            container_id=deployed["container_id"],
            container_name=deployed["container_name"],
            device_uuid=deployed["device_uuid"],
            display_name=deployed["display_name"],
        )
    except Exception as e:
        logger.error(f"Background Redroid deploy/provision failed: {e}")
        if deployed is None:
            # Provision worker already emits error event if deployed succeeded
            log_event(LogSeverity.ERROR, "REDROID", f"Gagal deploy container Redroid: {e}")
            telemetry_manager.emit_sync(
                "redroid_provisioning_event",
                {
                    "stage": "error",
                    "status": "error",
                    "title": "Redroid Deploy Error",
                    "message": f"Failed to deploy Redroid container: {e}",
                },
            )
        else:
            # Clean up half-provisioned container so ports & memory are not leaked
            try:
                await asyncio.to_thread(
                    redroid_manager.destroy_redroid_instance,
                    deployed["container_id"],
                    True,
                )
            except Exception as cleanup_err:
                logger.warning(f"Failed to clean up failed Redroid container: {cleanup_err}")


@router.get("/status")
async def get_redroid_status():
    """Check host OS, Docker daemon connectivity, Redroid image availability, and running containers."""
    return await asyncio.to_thread(redroid_manager.get_status)


@router.post("/install-image", response_model=ActionResponse)
async def install_redroid_image():
    """Trigger asynchronous `docker pull redroid/redroid:9.0.0-latest` with WebSocket progress updates."""
    status = await asyncio.to_thread(redroid_manager.get_status)
    if not status.get("supported"):
        raise HTTPException(
            status_code=400,
            detail="Redroid deployment is only supported on Linux with Docker installed.",
        )
    if not status.get("docker_available"):
        raise HTTPException(
            status_code=400,
            detail=status.get("error") or "Docker daemon is not available.",
        )
    if status.get("image_exists"):
        return ActionResponse(
            success=True,
            message=f"Image {REDROID_IMAGE} sudah terpasang.",
            data={"already_exists": True, "pulling": False},
        )
    if status.get("pulling_image"):
        return ActionResponse(
            success=True,
            message=f"Proses unduh {REDROID_IMAGE} sedang berjalan di latar belakang.",
            data={"already_exists": False, "pulling": True},
        )

    task = asyncio.create_task(_bg_pull_image())
    _track_task(task)
    return ActionResponse(
        success=True,
        message=f"Memulai unduhan image {REDROID_IMAGE} di latar belakang. Progres akan dikirim via WebSocket.",
        data={"already_exists": False, "pulling": True, "image": REDROID_IMAGE},
    )


@router.post("/deploy", response_model=ActionResponse)
async def deploy_redroid(payload: Optional[RedroidDeployRequest] = Body(default=None)):
    """
    Trigger asynchronous deployment + auto-provisioning (ADB connect -> anchor -> Nusuk Split APKs -> DB insert).
    Returns immediately while progress broadcasts over `/ws/telemetry`.
    """
    status = await asyncio.to_thread(redroid_manager.get_status)
    if not status.get("supported") or not status.get("docker_available"):
        raise HTTPException(
            status_code=400,
            detail=status.get("error")
            or "Redroid deployment is only supported on Linux with Docker installed.",
        )
    if status.get("pulling_image"):
        raise HTTPException(
            status_code=409,
            detail="Redroid image is currently being downloaded. Please wait until installation completes.",
        )
    if not status.get("image_exists"):
        raise HTTPException(
            status_code=400,
            detail=f"Image '{REDROID_IMAGE}' belum terpasang. Klik 'Install Redroid Image' terlebih dahulu.",
        )

    req_name = payload.name if payload else None
    req_port = payload.adb_port if payload else None

    task = asyncio.create_task(_bg_deploy_and_provision(display_name=req_name, adb_port=req_port))
    _track_task(task)

    return ActionResponse(
        success=True,
        message="Deploying new instance in the background. It will appear on the dashboard soon.",
        data={"queued": True, "name": req_name},
    )


@router.delete("/destroy/{container_id}", response_model=ActionResponse)
async def destroy_redroid(container_id: str):
    """Stops/destroys a Redroid container and soft-deletes its linked record in `instances`."""
    try:
        info = await asyncio.to_thread(redroid_manager.destroy_redroid_instance, container_id, True)
    except RedroidNotSupportedError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to destroy container: {e}")

    adb_port = info.get("adb_port")
    device_uuid = info.get("device_uuid")
    target_serial = f"127.0.0.1:{adb_port}" if adb_port else None

    if target_serial:
        try:
            await asyncio.to_thread(adb_manager.disconnect, target_serial)
        except Exception:
            pass

    # Locate matching active instance in DB and soft-delete cleanly via orchestrator
    soft_deleted_uuid = None
    db = SessionLocal()
    try:
        row = None
        if device_uuid:
            row = (
                db.query(Instance)
                .filter(Instance.uuid == device_uuid, Instance.is_deleted == False)  # noqa: E712
                .first()
            )
        if row is None and target_serial:
            row = (
                db.query(Instance)
                .filter(Instance.adb_serial == target_serial, Instance.is_deleted == False)  # noqa: E712
                .first()
            )
        if row is not None:
            soft_deleted_uuid = row.uuid
    finally:
        db.close()

    if soft_deleted_uuid:
        try:
            await asyncio.to_thread(orchestrator.delete_instance, soft_deleted_uuid)
        except Exception as e:
            logger.warning(f"Soft-delete for instance {soft_deleted_uuid} failed after container destroy: {e}")

    log_event(
        LogSeverity.INFO,
        "REDROID",
        f"Container Redroid '{info.get('container_name')}' dihancurkan"
        + (f" & instance {soft_deleted_uuid[:8]} di-soft-delete." if soft_deleted_uuid else "."),
        soft_deleted_uuid,
    )

    return ActionResponse(
        success=True,
        message=f"Container Redroid '{info.get('container_name')}' berhasil dihentikan dan dihapus.",
        data={**info, "soft_deleted_uuid": soft_deleted_uuid},
    )
