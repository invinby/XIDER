import asyncio

from aiohttp.test_utils import TestClient, TestServer

from enrollment_http import create_app
from enrollment_store import EnrollmentStore
from test_enrollment_store import request_pair


def test_http_pairing_request_notifies_owner_without_credential(tmp_path):
    async def run():
        store = EnrollmentStore(tmp_path / "pairs.db", clock=lambda: 1000)
        notices = []

        async def on_pending(data):
            notices.append(data)

        async with TestClient(TestServer(create_app(store, on_pending))) as client:
            request, _, _ = request_pair()
            response = await client.post("/x-dock/v1/request", json=request)
            assert response.status == 202
            assert response.headers["Cache-Control"] == "no-store"
            body = await response.json()
            assert body["state"] == "pending"
            assert body["request_id"] == request["request_id"]
            assert "proof" not in str(notices)
            assert "signing_key" not in str(notices)
            assert notices[0]["device_id"] == request["device_id"]

            bad = await client.post("/x-dock/v1/poll", json={"request_id": request["request_id"]})
            assert bad.status == 400

    asyncio.run(run())
