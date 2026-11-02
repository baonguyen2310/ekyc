"""Manual connectivity diagnostic. At most 3 real requests; synthetic image only.

Run explicitly with `docker compose run --rm --no-deps -T api python -`
and feed this script on stdin. Never run automatically at service startup.
"""

import asyncio
import io
import json
import os

from PIL import Image, ImageDraw

from app.provider import Credentials, VnptClient


async def main():
    values = [
        os.environ[k].strip() for k in ("VNPT_TOKEN_ID", "VNPT_TOKEN_KEY", "VNPT_ACCESS_TOKEN")
    ]
    values[2] = values[2].removeprefix("Bearer ").removeprefix("bearer ")
    client = VnptClient(Credentials(*values))
    result = []

    def summarize(stage, response):
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        safe = {"stage": stage, "http_status": response.status_code}
        if isinstance(payload, dict):
            for key in (
                "message",
                "errors",
                "error",
                "error_description",
                "statusCode",
                "description",
            ):
                value = payload.get(key)
                if value is not None:
                    text = json.dumps(value, ensure_ascii=False)
                    for secret in values:
                        text = text.replace(secret, "[redacted]")
                    safe[key] = text[:400]
            obj = payload.get("object")
            if isinstance(obj, dict):
                for key in ("msg", "liveness", "liveness_msg"):
                    if key in obj:
                        safe[key] = str(obj[key])[:200]
        result.append(safe)
        return payload

    try:
        image = Image.new("RGB", (800, 600), "white")
        ImageDraw.Draw(image).text(
            (50, 280), "INTERNAL CONNECTIVITY TEST - NO ID DOCUMENT", fill="black"
        )
        buffer = io.BytesIO()
        image.save(buffer, "JPEG")
        response = await client.client.post(
            "/file-service/v1/addFile",
            files={"file": ("connectivity-test.jpg", buffer.getvalue(), "image/jpeg")},
            data={
                "title": "internal-connectivity-test",
                "description": "Synthetic blank image; no personal data",
            },
        )
        data = summarize("upload_synthetic", response)
        obj = data.get("object", {}) if isinstance(data, dict) else {}
        value = obj.get("hash") if isinstance(obj, dict) else None
        if response.status_code != 200 or not value:
            return
        common = {
            "client_session": "WEB_EKYC_LAB_Browser_1_ConnectivityTest20261102",
            "token": "ConnectivityTest20261102",
        }
        for stage, path, payload in [
            (
                "ocr_front_synthetic",
                "/ai/v1/ocr/id/front",
                {"img_front": value, "type": -1, "validate_postcode": True},
            ),
            ("face_liveness_synthetic", "/ai/v1/face/liveness", {"img": value}),
        ]:
            response = await client.client.post(path, json={**common, **payload})
            summarize(stage, response)
            if response.status_code in {401, 403, 429}:
                break
    finally:
        await client.close()
        print(
            json.dumps(
                {"synthetic_image_only": True, "requests_sent": len(result), "results": result},
                ensure_ascii=False,
            )
        )


asyncio.run(main())
