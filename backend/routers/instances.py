import logging
from fastapi import APIRouter, HTTPException, Response

from backend.config import settings
from backend.schemas import (
    ActionResponse, InstanceTestRequest, InstancePairRequest, InstanceCreateRequest,
    InstanceUpdateRequest, InstanceStartRequest,
)
from backend.adb_manager import adb_manager
from backend.instance_manager import instance_manager
from backend.orchestrator import orchestrator, AI_MODES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/instances", tags=["Instances"])


@router.get("")
def list_instances():
    return {"items": instance_manager.snapshot()}


@router.get("/archived")
def list_archived_instances():
    return {"items": instance_manager.archived()}


@router.get("/discover")
def discover_connected_devices():
    """Scans attached USB / ADB devices via `adb devices -l`."""
    return {"items": instance_manager.discover_devices()}


@router.post("/pair")
def pair_wireless_device(payload: InstancePairRequest):
    """Pairs an Android 11+ Wireless Debugging device via `adb pair` + optional mDNS port detection."""
    try:
        res = instance_manager.pair_wireless(
            ip=payload.ip,
            pairing_port=payload.pairing_port,
            pairing_code=payload.pairing_code,
        )
        if not res["success"]:
            raise HTTPException(status_code=400, detail=res["message"])
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/test-connection")
def test_instance_connection(payload: InstanceTestRequest):
    try:
        return instance_manager.test_connection(
            ip=payload.ip,
            port=payload.port,
            connection_type=payload.connection_type,
            adb_serial=payload.adb_serial,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("", response_model=ActionResponse)
def add_instance(payload: InstanceCreateRequest):
    try:
        data = instance_manager.add_instance(
            name=payload.name,
            ip=payload.ip,
            port=payload.port,
            connection_type=payload.connection_type,
            adb_serial=payload.adb_serial,
            force_new_uuid=payload.force_new_uuid,
        )
        if data.get("restored"):
            msg = f"Perangkat lama dipulihkan: {data['instance']['name']}"
        elif data.get("relinked"):
            msg = f"Koneksi diperbarui (UUID lama): {data['instance']['name']} ({data['instance']['address']})"
        else:
            msg = f"Instance '{data['instance']['name']}' berhasil ditambahkan"
        return ActionResponse(success=True, message=msg, data=data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.patch("/{device_uuid}", response_model=ActionResponse)
def update_instance(device_uuid: str, payload: InstanceUpdateRequest):
    try:
        dto = instance_manager.update_instance(
            device_uuid=device_uuid,
            name=payload.name,
            ip=payload.ip,
            port=payload.port,
            connection_type=payload.connection_type,
            adb_serial=payload.adb_serial,
            live_preview_enabled=payload.live_preview_enabled,
            worker_active=orchestrator.is_active(device_uuid),
        )
        return ActionResponse(success=True, message="Pengaturan instance diperbarui", data=dto)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/{device_uuid}", response_model=ActionResponse)
def delete_instance(device_uuid: str):
    try:
        orchestrator.delete_instance(device_uuid)
        return ActionResponse(success=True, message="Instance dihapus (soft-delete). Riwayat tetap tersimpan.")
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except TimeoutError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/{device_uuid}/start", response_model=ActionResponse)
def start_instance(device_uuid: str, payload: InstanceStartRequest):
    if payload.mode in AI_MODES and not settings.ai_api_key:
        raise HTTPException(status_code=400, detail="Tidak dapat memulai: AI API Key belum dikonfigurasi di Settings.")
    try:
        res = orchestrator.start_many(
            [device_uuid],
            payload.mode,
            device_stop_conditions={device_uuid: {"stop_mode": payload.stop_mode, "stop_limit": payload.stop_limit}},
            stop_mode=payload.stop_mode or "limit",
            stop_limit=payload.stop_limit or 100,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if res["started"] or res["switched"] or res["already_running"]:
        return ActionResponse(success=True, message=res["message"], data=res)
    reason = res["skipped"][0]["reason"] if res["skipped"] else "Gagal memulai instance"
    raise HTTPException(status_code=400, detail=reason)


@router.post("/{device_uuid}/stop", response_model=ActionResponse)
def stop_instance(device_uuid: str):
    inst = instance_manager.get_instance(device_uuid)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance tidak ditemukan")
    res = orchestrator.stop_many([device_uuid])
    return ActionResponse(success=True, message=res["message"])


@router.post("/{device_uuid}/force-restart-app", response_model=ActionResponse)
def force_restart_app(device_uuid: str):
    ok, msg = orchestrator.force_restart_app(device_uuid)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return ActionResponse(success=True, message=msg)


@router.post("/{device_uuid}/force-restart-device", response_model=ActionResponse)
def force_restart_device(device_uuid: str):
    ok, msg = orchestrator.force_restart_device(device_uuid)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return ActionResponse(success=True, message=msg)


@router.post("/{device_uuid}/reconnect", response_model=ActionResponse)
def reconnect_instance(device_uuid: str):
    inst = instance_manager.get_instance(device_uuid)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance tidak ditemukan")
    ok = instance_manager.reconnect_now(device_uuid)
    if ok:
        return ActionResponse(success=True, message=f"Berhasil terhubung ke {inst.name} ({inst.address})")
    raise HTTPException(status_code=503, detail=f"Gagal terhubung ke {inst.name} ({inst.address})")


@router.get("/{device_uuid}/screenshot")
def get_instance_screenshot(device_uuid: str):
    inst = instance_manager.get_instance(device_uuid)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance tidak ditemukan")

    # CRITICAL (blueprint 3.3 & 5.6 #6): Hard block before touching ADB when preview is OFF
    if not inst.live_preview_enabled:
        raise HTTPException(status_code=409, detail="preview_disabled")

    rt = instance_manager.rt(device_uuid)
    if rt.status in ("OFFLINE", "REBOOTING"):
        raise HTTPException(status_code=503, detail=f"Perangkat sedang {rt.status}")

    raw_bytes = adb_manager.take_screenshot_bytes(inst.address)
    if not raw_bytes:
        raise HTTPException(status_code=503, detail="Gagal mengambil screenshot dari perangkat")

    return Response(
        content=raw_bytes,
        media_type="image/png",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
