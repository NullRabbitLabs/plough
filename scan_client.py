import logging
from dataclasses import dataclass, field

import httpx

from config import Config

logger = logging.getLogger(__name__)


@dataclass
class ScanSubmission:
    scan_id: str
    ip: str
    cdn_blocked: bool = False
    cdn_provider: str = ""


class ScanClientError(Exception):
    pass


class ScanClient:
    def __init__(self, config: Config, client: httpx.AsyncClient) -> None:
        self.config = config
        self.client = client

    async def submit(
        self, ip: str, metadata: dict, protocol: str | None = None
    ) -> ScanSubmission:
        url = f"{self.config.scan_api_url}/api/v1/scans"
        headers = {}
        if self.config.scan_api_token:
            headers["Authorization"] = f"Bearer {self.config.scan_api_token}"

        payload: dict = {
            "host_ip": ip,
            "scan_mode": "limpet",
            "max_iterations": 4,
            "scan_intensity": "regular",
            "metadata": metadata,
            "force_cdn": False,
        }
        if protocol is not None:
            payload["protocol"] = protocol

        try:
            resp = await self.client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ScanClientError(
                f"Scan API error {exc.response.status_code} for {ip}"
            ) from exc

        body = resp.json()
        if body.get("cdn_blocked"):
            return ScanSubmission(
                scan_id="",
                ip=ip,
                cdn_blocked=True,
                cdn_provider=body.get("cdn_provider", ""),
            )
        return ScanSubmission(scan_id=body.get("scan_id", ""), ip=ip)
