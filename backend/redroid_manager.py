"""
Redroid Docker Orchestration Manager (Linux Only).
Communicates with the local Docker daemon via Docker SDK for Python to manage
headless Redroid Android 11 containers (`redroid/redroid:11.0.0-latest`).

Designed to fail gracefully on non-Linux hosts (Windows/macOS) or when the Docker
SDK/daemon is unavailable.
"""

import logging
import os
import platform
import shutil
import socket
import sys
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

try:
    import docker
    from docker.errors import APIError, DockerException, ImageNotFound, NotFound

    DOCKER_SDK_AVAILABLE = True
except ImportError:
    docker = None  # type: ignore
    DockerException = Exception  # type: ignore
    APIError = Exception  # type: ignore
    ImageNotFound = Exception  # type: ignore
    NotFound = Exception  # type: ignore
    DOCKER_SDK_AVAILABLE = False

logger = logging.getLogger(__name__)

DEFAULT_ANDROID_VERSION = "11.0.0-latest"
REDROID_IMAGE = f"redroid/redroid:{DEFAULT_ANDROID_VERSION}"
CONTAINER_PREFIX = "nusuk_redroid_"
ADB_PORT_MIN = 5556
ADB_PORT_MAX = 6000

REDROID_BOOT_ARGS = [
    "androidboot.redroid_width=720",
    "androidboot.redroid_height=1280",
    "androidboot.redroid_dpi=320",
    "androidboot.redroid_fps=30",
    "androidboot.redroid_gpu.mode=guest",
    "redroid.memory.size=1536M",
    "ro.product.cpu.abilist=x86_64,arm64-v8a",
    "ro.product.manufacturer=Samsung",
    "ro.product.model=SM-G998B",
    "ro.product.name=p3sxx",
    "ro.product.device=p3s",
    "ro.build.tags=release-keys",
    "ro.build.type=user",
    "ro.hardware=qcom",
    "ro.product.board=msmnile",
]


class RedroidNotSupportedError(RuntimeError):
    """Raised when Redroid operations are attempted on a non-Linux host or without Docker."""


class RedroidManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._port_lock = threading.Lock()
        self._pulling_image = False
        self._pull_status_message: Optional[str] = None
        self._reserved_ports: Dict[int, float] = {}

    @staticmethod
    def is_linux() -> bool:
        return sys.platform.startswith("linux")

    @staticmethod
    def check_binder_support() -> bool:
        """Pre-flight check for Linux kernel Binder support (/dev/binderfs or /dev/binder)."""
        return os.path.exists("/dev/binderfs") or os.path.exists("/dev/binder")

    def ensure_binder_support(self) -> None:
        if self.is_linux() and not self.check_binder_support():
            raise RedroidNotSupportedError(
                "Kernel module Binder tidak terdeteksi di Host OS Ubuntu. "
                "OS tidak mendukung Redroid. Harap install linux-modules-extra."
            )

    @property
    def is_pulling_image(self) -> bool:
        with self._lock:
            return self._pulling_image

    # ------------------------------------------------------------------ Docker client
    def _get_client(self):
        """Return a verified local Docker client, or raise RedroidNotSupportedError."""
        if not self.is_linux():
            raise RedroidNotSupportedError(
                "Redroid deployment is only supported on Linux with Docker installed."
            )
        if not DOCKER_SDK_AVAILABLE or docker is None:
            raise RedroidNotSupportedError(
                "Python Docker SDK ('docker') is not installed. Run: pip install docker"
            )
        try:
            client = docker.from_env()
            client.ping()
            return client
        except Exception as e:
            raise RedroidNotSupportedError(
                f"Cannot connect to local Docker daemon. Ensure Docker is running and user has permission: {e}"
            )

    def _try_get_client(self):
        """Safe variant returning (client, error_message)."""
        try:
            return self._get_client(), None
        except Exception as e:
            return None, str(e)

    @staticmethod
    def resolve_image(android_version: Optional[str] = None) -> str:
        ver = (android_version or DEFAULT_ANDROID_VERSION).strip() or DEFAULT_ANDROID_VERSION
        return f"redroid/redroid:{ver}"

    # ------------------------------------------------------------------ Status & Image
    def check_image_exists(self, client=None, android_version: Optional[str] = None) -> bool:
        image_name = self.resolve_image(android_version)
        close_after = False
        if client is None:
            client, _ = self._try_get_client()
            if client is None:
                return False
            close_after = True
        try:
            client.images.get(image_name)
            return True
        except ImageNotFound:
            return False
        except Exception as e:
            logger.debug(f"Error checking Redroid image: {e}")
            return False
        finally:
            if close_after:
                try:
                    client.close()
                except Exception:
                    pass

    def get_status(self, android_version: Optional[str] = None) -> Dict[str, Any]:
        """Returns comprehensive host OS, Docker daemon, image, and container status."""
        image_name = self.resolve_image(android_version)
        os_name = platform.system()
        if not self.is_linux():
            return {
                "supported": False,
                "os": os_name,
                "docker_sdk_installed": DOCKER_SDK_AVAILABLE,
                "docker_available": False,
                "image_name": image_name,
                "image_exists": False,
                "pulling_image": False,
                "pull_status": None,
                "active_containers": 0,
                "containers": [],
                "ready": False,
                "error": "Redroid deployment is only supported on Linux with Docker installed.",
            }

        client, err_msg = self._try_get_client()
        if client is None:
            return {
                "supported": True,
                "os": os_name,
                "docker_sdk_installed": DOCKER_SDK_AVAILABLE,
                "docker_available": False,
                "image_name": image_name,
                "image_exists": False,
                "pulling_image": self.is_pulling_image,
                "pull_status": self._pull_status_message,
                "active_containers": 0,
                "containers": [],
                "ready": False,
                "error": err_msg or "Docker daemon is not available.",
            }

        try:
            image_exists = self.check_image_exists(client, android_version=android_version)
            containers = self._list_containers_with_client(client)
            return {
                "supported": True,
                "os": os_name,
                "docker_sdk_installed": DOCKER_SDK_AVAILABLE,
                "docker_available": True,
                "image_name": image_name,
                "image_exists": image_exists,
                "pulling_image": self.is_pulling_image,
                "pull_status": self._pull_status_message,
                "active_containers": len(containers),
                "containers": containers,
                "ready": image_exists and not self.is_pulling_image,
                "error": None if image_exists else f"Image '{image_name}' is not installed yet.",
            }
        except Exception as e:
            logger.error(f"Failed to query Redroid status: {e}")
            return {
                "supported": True,
                "os": os_name,
                "docker_sdk_installed": DOCKER_SDK_AVAILABLE,
                "docker_available": False,
                "image_name": image_name,
                "image_exists": False,
                "pulling_image": self.is_pulling_image,
                "pull_status": self._pull_status_message,
                "active_containers": 0,
                "containers": [],
                "ready": False,
                "error": str(e),
            }
        finally:
            try:
                client.close()
            except Exception:
                pass

    def pull_image(
        self,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        android_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Pulls `redroid/redroid:<android_version>` synchronously (call inside a thread / asyncio.to_thread).
        Streams progress events to `progress_callback` if provided.
        """
        image_name = self.resolve_image(android_version)
        with self._lock:
            if self._pulling_image:
                return {"success": True, "already_pulling": True, "message": "Image pull already in progress."}
            self._pulling_image = True
            self._pull_status_message = f"Pulling {image_name}..."

        client = None
        try:
            client = self._get_client()
            repo, tag = image_name.split(":", 1)
            if progress_callback:
                progress_callback({
                    "stage": "pull_image",
                    "status": "progress",
                    "title": "Redroid Image",
                    "message": f"Downloading Docker image {image_name}...",
                })

            last_emit = 0.0
            layers_seen: Dict[str, str] = {}
            for chunk in client.api.pull(repo, tag=tag, stream=True, decode=True):
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("error"):
                    raise RuntimeError(str(chunk["error"]))
                layer_id = chunk.get("id", "")
                st = chunk.get("status", "")
                prog = chunk.get("progress", "")
                if layer_id and st:
                    layers_seen[layer_id] = st
                msg = f"{st} {layer_id} {prog}".strip() if st else f"Pulling {image_name}..."
                self._pull_status_message = msg

                now = time.time()
                if progress_callback and (now - last_emit >= 2.0):
                    last_emit = now
                    done_layers = sum(
                        1 for v in layers_seen.values()
                        if v in ("Pull complete", "Already exists")
                    )
                    total_layers = max(1, len(layers_seen))
                    progress_callback({
                        "stage": "pull_image",
                        "status": "progress",
                        "title": "Redroid Image",
                        "message": f"Pulling {image_name}: {st or 'Downloading...'} ({done_layers}/{total_layers} layers)",
                    })

            self._pull_status_message = "Image ready"
            if progress_callback:
                progress_callback({
                    "stage": "pull_image",
                    "status": "success",
                    "title": "Redroid Image Ready",
                    "message": f"Successfully installed {image_name}. You can now deploy instances.",
                })
            return {"success": True, "image": image_name, "message": f"Image {image_name} installed."}
        except Exception as e:
            logger.error(f"Redroid image pull failed: {e}")
            self._pull_status_message = f"Pull failed: {e}"
            if progress_callback:
                progress_callback({
                    "stage": "pull_image",
                    "status": "error",
                    "title": "Redroid Image Error",
                    "message": f"Failed to pull {image_name}: {e}",
                })
            raise
        finally:
            with self._lock:
                self._pulling_image = False
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------ Port Allocation
    @staticmethod
    def _is_tcp_port_free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return True
            except OSError:
                return False

    def _collect_used_ports(self, client) -> set:
        used = set()
        now = time.time()
        # Clean up stale short-term reservations (> 120s)
        stale = [p for p, ts in self._reserved_ports.items() if now - ts > 120]
        for p in stale:
            self._reserved_ports.pop(p, None)
        used.update(self._reserved_ports.keys())

        # Ports mapped by active containers
        try:
            for item in self._list_containers_with_client(client):
                if item.get("adb_port"):
                    used.add(int(item["adb_port"]))
        except Exception:
            pass

        # Ports registered on localhost in DB (active instances)
        try:
            from backend.database import SessionLocal
            from backend.models import Instance

            db = SessionLocal()
            try:
                rows = (
                    db.query(Instance)
                    .filter(Instance.is_deleted == False)  # noqa: E712
                    .all()
                )
                for row in rows:
                    if row.ip in ("127.0.0.1", "localhost") and row.port:
                        used.add(int(row.port))
            finally:
                db.close()
        except Exception:
            pass

        return used

    def allocate_adb_port(self, client) -> int:
        with self._port_lock:
            used = self._collect_used_ports(client)
            for port in range(ADB_PORT_MIN, ADB_PORT_MAX + 1):
                if port in used:
                    continue
                if self._is_tcp_port_free(port):
                    self._reserved_ports[port] = time.time()
                    return port
            raise RuntimeError(f"No free ADB ports available in range {ADB_PORT_MIN}-{ADB_PORT_MAX}.")

    # ------------------------------------------------------------------ Deploy / Destroy / List
    def deploy_redroid_instance(
        self,
        container_name: Optional[str] = None,
        adb_port: Optional[int] = None,
        display_name: Optional[str] = None,
        bind_to_uuid: Optional[str] = None,
        android_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Spawns a new privileged Redroid Android container equivalent to:
        docker run -itd --privileged --pull always -v ~/data_<uuid>:/data
          -p <adb_port>:5555 --name <container_name> redroid/redroid:<android_version>
          androidboot.redroid_width=720 androidboot.redroid_height=1280
          androidboot.redroid_dpi=320 androidboot.redroid_fps=30
          androidboot.redroid_gpu.mode=guest
          redroid.memory.size=1536M ro.product.cpu.abilist=x86_64,arm64-v8a
        """
        image_name = self.resolve_image(android_version)
        self.ensure_binder_support()
        client = self._get_client()
        try:
            generated_uuid = str(uuid.uuid4())
            device_uuid = (bind_to_uuid or "").strip() or generated_uuid
            uid_short = generated_uuid.split("-")[0]

            if container_name:
                cname = container_name.strip()
                if not cname.startswith(CONTAINER_PREFIX):
                    cname = f"{CONTAINER_PREFIX}{cname}"
            else:
                cname = f"{CONTAINER_PREFIX}{uid_short}"

            if adb_port is None:
                port = self.allocate_adb_port(client)
            else:
                port = int(adb_port)
                if not (1024 <= port <= 65535):
                    raise ValueError(f"Invalid ADB port: {port}")

            # Ensure image exists locally (pull if missing, mimicking --pull)
            if not self.check_image_exists(client, android_version=android_version):
                logger.info(f"Image {image_name} not found locally; pulling before deploy...")
                repo, tag = image_name.split(":", 1)
                client.images.pull(repo, tag=tag)

            data_dir = os.path.expanduser(f"~/data_{uid_short}")
            os.makedirs(data_dir, exist_ok=True)

            logger.info(
                f"Deploying Redroid container '{cname}' ({image_name}) on 127.0.0.1:{port} (data={data_dir})..."
            )

            container = client.containers.run(
                image=image_name,
                command=REDROID_BOOT_ARGS,
                name=cname,
                detach=True,
                stdin_open=True,
                tty=True,
                auto_remove=False,
                privileged=True,
                volumes={data_dir: {"bind": "/data", "mode": "rw"}},
                ports={"5555/tcp": port},
                labels={
                    "managed_by": "nusuk_automation_hub",
                    "nusuk.adb_port": str(port),
                    "nusuk.data_dir": data_dir,
                    "nusuk.device_uuid": device_uuid,
                },
            )

            friendly_name = (display_name or "").strip()[:64] or f"Redroid-{port}"
            return {
                "container_id": container.id,
                "short_id": container.short_id,
                "container_name": cname,
                "adb_port": port,
                "host": "127.0.0.1",
                "adb_serial": f"127.0.0.1:{port}",
                "data_dir": data_dir,
                "device_uuid": device_uuid,
                "display_name": friendly_name,
            }
        except DockerException as e:
            logger.error(f"Docker error while deploying Redroid: {e}")
            raise RuntimeError(f"Failed to deploy Redroid container: {e}")
        finally:
            try:
                client.close()
            except Exception:
                pass

    def destroy_redroid_instance(
        self,
        container_id_or_name: str,
        remove_volume: bool = True,
    ) -> Dict[str, Any]:
        """Stops a running Redroid container (auto-removed via --rm) and cleans up its data volume."""
        client = self._get_client()
        try:
            try:
                container = client.containers.get(container_id_or_name)
            except NotFound:
                raise LookupError(f"Redroid container '{container_id_or_name}' not found.")

            attrs = container.attrs or {}
            labels = (attrs.get("Config") or {}).get("Labels") or {}
            cname = container.name
            cid = container.id
            adb_port = self._extract_host_port(attrs, labels)
            device_uuid = labels.get("nusuk.device_uuid")
            data_dir = labels.get("nusuk.data_dir")

            # Fallback: inspect mounts for /data bind
            if not data_dir:
                for m in attrs.get("Mounts") or []:
                    if m.get("Destination") == "/data" and m.get("Source"):
                        data_dir = m["Source"]
                        break

            logger.info(f"Stopping & removing Redroid container '{cname}' ({cid[:12]})...")
            try:
                container.stop(timeout=10)
            except NotFound:
                pass
            except Exception as e:
                logger.debug(f"Container stop warning for '{cname}': {e}")

            # Force remove container (handles both --rm and non-rm or stuck containers)
            try:
                container.remove(force=True)
            except NotFound:
                pass
            except Exception as e:
                logger.debug(f"Container force-remove warning for '{cname}': {e}")

            if adb_port:
                with self._port_lock:
                    self._reserved_ports.pop(int(adb_port), None)

            removed_data_dir = False
            if remove_volume and data_dir:
                home_dir = os.path.abspath(os.path.expanduser("~"))
                abs_data = os.path.abspath(data_dir)
                base_name = os.path.basename(abs_data)
                # Guard: only delete directories matching ~/data_*
                if abs_data.startswith(home_dir) and base_name.startswith("data_") and os.path.isdir(abs_data):
                    try:
                        shutil.rmtree(abs_data, ignore_errors=True)
                        removed_data_dir = True
                    except Exception as e:
                        logger.warning(f"Could not remove volume directory {abs_data}: {e}")

            return {
                "container_id": cid,
                "container_name": cname,
                "adb_port": adb_port,
                "device_uuid": device_uuid,
                "data_dir": data_dir,
                "removed_data_dir": removed_data_dir,
            }
        finally:
            try:
                client.close()
            except Exception:
                pass

    @staticmethod
    def _extract_host_port(attrs: Dict[str, Any], labels: Dict[str, Any]) -> Optional[int]:
        ports = (attrs.get("NetworkSettings") or {}).get("Ports") or {}
        bindings = ports.get("5555/tcp")
        if isinstance(bindings, list) and bindings:
            hp = bindings[0].get("HostPort")
            if hp and str(hp).isdigit():
                return int(hp)
        lbl_port = labels.get("nusuk.adb_port")
        if lbl_port and str(lbl_port).isdigit():
            return int(lbl_port)
        return None

    def _list_containers_with_client(self, client, include_stopped: bool = True) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        try:
            containers = client.containers.list(all=include_stopped, filters={"name": CONTAINER_PREFIX})
        except Exception as e:
            logger.warning(f"Error listing Redroid containers: {e}")
            return results

        for c in containers:
            try:
                cname = (c.name or "").lstrip("/")
                if not cname.startswith(CONTAINER_PREFIX):
                    continue
                attrs = c.attrs or {}
                labels = (attrs.get("Config") or {}).get("Labels") or {}
                adb_port = self._extract_host_port(attrs, labels)
                c_status = (c.status or "unknown").lower()
                results.append({
                    "container_id": c.id,
                    "short_id": c.short_id,
                    "name": cname,
                    "container_name": cname,
                    "status": c_status,
                    "adb_port": adb_port,
                    "adb_serial": f"127.0.0.1:{adb_port}" if adb_port else None,
                    "device_uuid": labels.get("nusuk.device_uuid"),
                    "data_dir": labels.get("nusuk.data_dir"),
                })
            except Exception:
                continue
        return results

    def list_redroid_instances(self, include_stopped: bool = True) -> List[Dict[str, Any]]:
        """Returns list of all `nusuk_redroid_*` containers (running & exited) with port mappings."""
        client, _ = self._try_get_client()
        if client is None:
            return []
        try:
            return self._list_containers_with_client(client, include_stopped=include_stopped)
        finally:
            try:
                client.close()
            except Exception:
                pass

    def start_redroid_instance(self, container_id_or_name: str) -> Dict[str, Any]:
        """Starts an exited Redroid container."""
        client = self._get_client()
        try:
            try:
                container = client.containers.get(container_id_or_name)
            except NotFound:
                raise LookupError(f"Redroid container '{container_id_or_name}' not found.")

            cname = (container.name or "").lstrip("/")
            if (container.status or "").lower() != "running":
                container.start()
                container.reload()

            attrs = container.attrs or {}
            labels = (attrs.get("Config") or {}).get("Labels") or {}
            adb_port = self._extract_host_port(attrs, labels)
            return {
                "container_id": container.id,
                "name": cname,
                "container_name": cname,
                "status": (container.status or "running").lower(),
                "adb_port": adb_port,
                "adb_serial": f"127.0.0.1:{adb_port}" if adb_port else None,
            }
        finally:
            try:
                client.close()
            except Exception:
                pass

    def stop_redroid_instance(self, container_id_or_name: str) -> Dict[str, Any]:
        """Stops a running Redroid container."""
        client = self._get_client()
        try:
            try:
                container = client.containers.get(container_id_or_name)
            except NotFound:
                raise LookupError(f"Redroid container '{container_id_or_name}' not found.")

            cname = (container.name or "").lstrip("/")
            attrs = container.attrs or {}
            labels = (attrs.get("Config") or {}).get("Labels") or {}
            adb_port = self._extract_host_port(attrs, labels)

            if (container.status or "").lower() == "running":
                container.stop(timeout=10)
                try:
                    container.reload()
                    new_status = (container.status or "exited").lower()
                except NotFound:
                    new_status = "removed"
            else:
                new_status = (container.status or "exited").lower()

            return {
                "container_id": container.id,
                "name": cname,
                "container_name": cname,
                "status": new_status,
                "adb_port": adb_port,
                "adb_serial": f"127.0.0.1:{adb_port}" if adb_port else None,
            }
        finally:
            try:
                client.close()
            except Exception:
                pass

    def auto_start_containers(self) -> None:
        """Auto-start any stopped nusuk_redroid_* Docker containers on backend startup."""
        client, _ = self._try_get_client()
        if client is None:
            return
        try:
            containers = client.containers.list(all=True)
            for container in containers:
                cname = (container.name or "").lstrip("/")
                status = (container.status or "").lower()
                if cname.startswith(CONTAINER_PREFIX) and status != "running":
                    try:
                        logger.info(f"Auto-starting Redroid container: {cname}")
                        container.start()
                    except Exception as e:
                        logger.warning(f"Failed to auto-start Redroid container '{cname}': {e}")
        except Exception as e:
            logger.warning(f"Error listing Redroid containers for auto-start: {e}")
        finally:
            try:
                client.close()
            except Exception:
                pass


redroid_manager = RedroidManager()
