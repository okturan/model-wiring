from __future__ import annotations

import time
import unittest
from urllib.parse import parse_qs, urlparse

from model_wiring import (
    CredentialMaterial,
    CredentialProfile,
    OAuthClient,
    OAuthProviderConfig,
)
from model_wiring.oauth import AuthorizationPending, OAuthError


class QueueTransport:
    def __init__(self, *responses: dict) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, url: str, params: dict, headers: dict, timeout: float) -> dict:
        self.calls.append((url, dict(params), dict(headers)))
        return self.responses.pop(0)


class OAuthTests(unittest.TestCase):
    def config(self, **values: object) -> OAuthProviderConfig:
        base = {
            "client_id": "public-client",
            "authorization_endpoint": "https://auth.example/authorize",
            "token_endpoint": "https://auth.example/token",
            "device_authorization_endpoint": "https://auth.example/device",
            "redirect_uri": "http://127.0.0.1:8844/callback",
            "scopes": ("models.read",),
        }
        base.update(values)
        return OAuthProviderConfig(**base)  # type: ignore[arg-type]

    def test_authorization_code_flow_uses_pkce_and_checks_state(self) -> None:
        transport = QueueTransport(
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
                "token_type": "Bearer",
            }
        )
        client = OAuthClient(self.config(), transport=transport)
        request = client.begin_authorization(state="expected")
        parameters = parse_qs(urlparse(request.url).query)

        self.assertEqual(["S256"], parameters["code_challenge_method"])
        self.assertNotIn(request.code_verifier, request.url)
        self.assertNotIn(request.code_verifier, repr(request))
        self.assertNotIn("code_verifier", request.public_dict())
        with self.assertRaises(OAuthError):
            client.exchange_code("code", request, returned_state="wrong")

        material = client.exchange_code("code", request, returned_state="expected")
        self.assertEqual("access", material.reveal("access_token"))
        sent = transport.calls[0][1]
        self.assertEqual(request.code_verifier, sent["code_verifier"])
        material.wipe()

    def test_device_flow_exposes_user_code_but_redacts_device_code(self) -> None:
        transport = QueueTransport(
            {
                "device_code": "private-device",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://auth.example/device/verify",
                "expires_in": 600,
                "interval": 3,
            },
            {"error": "authorization_pending"},
            {"access_token": "ready", "expires_in": 3600},
        )
        client = OAuthClient(self.config(), transport=transport)
        authorization = client.begin_device_authorization()

        self.assertEqual("ABCD-EFGH", authorization.public_dict()["user_code"])
        self.assertNotIn("private-device", repr(authorization))
        self.assertNotIn("device_code", authorization.public_dict())
        with self.assertRaises(AuthorizationPending):
            client.poll_device(authorization)
        material = client.poll_device(authorization)
        self.assertEqual("ready", material.reveal())
        material.wipe()
        authorization.wipe()

    def test_refresh_preserves_old_refresh_token_when_not_rotated(self) -> None:
        transport = QueueTransport({"access_token": "new", "expires_in": 3600})
        client = OAuthClient(self.config(), transport=transport)
        old = CredentialMaterial(
            {"access_token": "old", "refresh_token": "keep-me"},
            expires_at=time.time() - 1,
        )
        profile = CredentialProfile(
            id="oauth",
            provider_id="provider",
            auth_kind="oauth",
            billing_kind="subscription",
            secret_ref="oauth",
        )

        refreshed = client.refresh(profile, old)

        self.assertEqual("new", refreshed.reveal("access_token"))
        self.assertEqual("keep-me", refreshed.reveal("refresh_token"))
        self.assertEqual("keep-me", transport.calls[0][1]["refresh_token"])
        old.wipe()
        refreshed.wipe()


if __name__ == "__main__":
    unittest.main()
