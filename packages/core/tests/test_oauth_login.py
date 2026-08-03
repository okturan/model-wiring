from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from model_wiring import Catalog, MemorySecretStore, ProfileRegistry
from model_wiring.login import (
    LoginBroker,
    LoginResult,
    OpenUrlPrompt,
    UserCodePrompt,
)
from model_wiring.oauth import OAuthError
from model_wiring.oauth_login import LoopbackRedirect

# Both fixtures model a consuming application that registered its own OAuth
# client, so their routes declare the posture that permits one; an undeclared
# route offers delegated import only and never reaches these drivers.
PKCE_PROVIDER = {
    "acme-cloud": {
        "name": "Acme Cloud",
        "env": ["ACME_API_KEY"],
        "doc": "https://acme.example/docs",
        "models": {"a-1": {"name": "A1"}},
        "access_routes": [
            {
                "id": "subscription",
                "kind": "oauth",
                "billing_kind": "subscription",
                "label": "Acme subscription",
                "terms_posture": "third_party_permitted",
                "driver": "oauth_pkce",
                "metadata": {
                    "oauth": {
                        "client_id": "public-client",
                        "authorization_endpoint": "https://acme.example/authorize",
                        "token_endpoint": "https://acme.example/token",
                        "scopes": ["read", "write"],
                    }
                },
            }
        ],
    }
}

DEVICE_PROVIDER = {
    "acme-device": {
        "name": "Acme Device",
        "models": {"a-1": {"name": "A1"}},
        "access_routes": [
            {
                "id": "device",
                "kind": "oauth",
                "billing_kind": "subscription",
                "label": "Acme device sign-in",
                "terms_posture": "third_party_permitted",
                "driver": "oauth_device",
                "metadata": {
                    "oauth": {
                        "client_id": "public-client",
                        "authorization_endpoint": "https://acme.example/authorize",
                        "token_endpoint": "https://acme.example/token",
                        "device_authorization_endpoint": "https://acme.example/device",
                    }
                },
            }
        ],
    }
}


def broker_for(
    raw: dict, transport=None
) -> tuple[LoginBroker, ProfileRegistry, MemorySecretStore]:
    registry = ProfileRegistry(Path(tempfile.mkdtemp()) / "profiles.sqlite3")
    store = MemorySecretStore()
    catalog = Catalog.from_models_dev(
        raw, fetched_at="2026-08-02T00:00:00Z", include_default_overlays=False
    )
    return (
        LoginBroker(
            catalog,
            profiles=registry,
            stores={"memory": store},
            default_store="memory",
            oauth_transport=transport,
        ),
        registry,
        store,
    )


def token_transport(**extra: object):
    """Fake token endpoint that records what it was asked for."""

    calls: list[dict] = []

    def transport(url, parameters, headers, timeout):
        calls.append({"url": url, "parameters": dict(parameters)})
        if url.endswith("/device"):
            return {
                "verification_uri": "https://acme.example/activate",
                "user_code": "WXYZ-1234",
                "device_code": "device-secret",
                "expires_in": 600,
                "interval": 1,
            }
        return {
            "access_token": "access-token-value",
            "refresh_token": "refresh-token-value",
            "expires_in": 3600,
            **extra,
        }

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


class AuthorizationCodeLoginTests(unittest.TestCase):
    def test_beginning_a_browser_login_returns_an_authorization_url(self) -> None:
        login, _, _ = broker_for(PKCE_PROVIDER, token_transport())

        session = login.begin("acme-cloud", route_id="subscription")

        self.assertIsInstance(session.prompt, OpenUrlPrompt)
        parsed = urlparse(session.prompt.url)
        query = parse_qs(parsed.query)
        self.assertEqual(
            "https://acme.example/authorize", f"https://{parsed.netloc}{parsed.path}"
        )
        self.assertEqual(["public-client"], query["client_id"])
        self.assertEqual(["S256"], query["code_challenge_method"])
        self.assertEqual(["read write"], query["scope"])

    def test_the_authorization_url_never_exposes_the_code_verifier(self) -> None:
        login, _, _ = broker_for(PKCE_PROVIDER, token_transport())

        session = login.begin("acme-cloud", route_id="subscription")

        payload = json.dumps(session.to_dict())
        self.assertNotIn(session.private["request"].code_verifier, payload)

    def test_exchanging_the_code_stores_tokens_and_a_subscription_profile(self) -> None:
        transport = token_transport()
        login, registry, store = broker_for(PKCE_PROVIDER, transport)
        session = login.begin("acme-cloud", route_id="subscription")
        state = session.private["request"].state

        result = login.advance(session, {"code": "auth-code", "state": state})

        self.assertIsInstance(result, LoginResult)
        profile = registry.get(result.profile.id)
        self.assertEqual("oauth", profile.auth_kind)
        self.assertEqual("subscription", profile.billing_kind)
        material = store.get(profile.secret_ref)
        self.assertEqual("access-token-value", material.reveal("access_token"))
        self.assertEqual("refresh-token-value", material.reveal("refresh_token"))

    def test_a_mismatched_state_is_refused_before_any_token_request(self) -> None:
        transport = token_transport()
        login, registry, _ = broker_for(PKCE_PROVIDER, transport)
        session = login.begin("acme-cloud", route_id="subscription")

        with self.assertRaises(OAuthError):
            login.advance(session, {"code": "auth-code", "state": "forged"})

        self.assertEqual([], transport.calls)
        self.assertEqual((), tuple(registry.list()))

    def test_the_stored_profile_records_expiry_so_refresh_can_be_scheduled(
        self,
    ) -> None:
        login, registry, store = broker_for(PKCE_PROVIDER, token_transport())
        session = login.begin("acme-cloud", route_id="subscription")
        state = session.private["request"].state

        result = login.advance(session, {"code": "auth-code", "state": state})

        material = store.get(registry.get(result.profile.id).secret_ref)
        self.assertIsNotNone(material.expires_at)
        self.assertFalse(material.expired())


class DeviceLoginTests(unittest.TestCase):
    def test_device_login_shows_a_code_and_a_verification_page(self) -> None:
        login, _, _ = broker_for(DEVICE_PROVIDER, token_transport())

        session = login.begin("acme-device", route_id="device")

        self.assertIsInstance(session.prompt, UserCodePrompt)
        self.assertEqual("WXYZ-1234", session.prompt.user_code)
        self.assertEqual(
            "https://acme.example/activate", session.prompt.verification_uri
        )

    def test_the_device_code_is_never_shown_to_the_user(self) -> None:
        login, _, _ = broker_for(DEVICE_PROVIDER, token_transport())

        session = login.begin("acme-device", route_id="device")

        self.assertNotIn("device-secret", json.dumps(session.to_dict()))

    def test_polling_after_approval_stores_the_credential(self) -> None:
        login, registry, store = broker_for(DEVICE_PROVIDER, token_transport())
        session = login.begin("acme-device", route_id="device")

        result = login.advance(session, {})

        material = store.get(registry.get(result.profile.id).secret_ref)
        self.assertEqual("access-token-value", material.reveal("access_token"))

    def test_it_keeps_polling_while_the_user_has_not_approved_yet(self) -> None:
        login, registry, store, slept = self.polling_broker(
            ["authorization_pending"] * 2
        )
        session = login.begin("acme-device", route_id="device")

        result = login.advance(session, {})

        material = store.get(registry.get(result.profile.id).secret_ref)
        self.assertEqual("approved-at-last", material.reveal("access_token"))
        self.assertEqual([5.0, 5.0], slept)

    def test_a_slow_down_response_increases_the_polling_interval(self) -> None:
        login, _, _, slept = self.polling_broker(
            ["authorization_pending", "slow_down", "authorization_pending"]
        )
        session = login.begin("acme-device", route_id="device")

        login.advance(session, {})

        self.assertEqual([5.0, 10.0, 10.0], slept)

    def polling_broker(self, errors: list[str]):
        """Device broker whose provider replies with ``errors`` before approving."""

        queue = list(errors)
        slept: list[float] = []

        def transport(url, parameters, headers, timeout):
            if url.endswith("/device"):
                return {
                    "verification_uri": "https://acme.example/activate",
                    "user_code": "WXYZ-1234",
                    "device_code": "device-secret",
                    "expires_in": 600,
                    "interval": 5,
                }
            if queue:
                return {"error": queue.pop(0)}
            return {"access_token": "approved-at-last", "expires_in": 3600}

        login, registry, store = broker_for(DEVICE_PROVIDER, transport)
        login.drivers["oauth_device"].sleep = slept.append
        return login, registry, store, slept


class LoopbackRedirectTests(unittest.TestCase):
    def test_it_captures_the_authorization_code_from_the_browser(self) -> None:
        with LoopbackRedirect() as redirect:

            def visit() -> None:
                urllib.request.urlopen(
                    f"{redirect.redirect_uri}?code=the-code&state=the-state", timeout=5
                ).read()

            threading.Thread(target=visit, daemon=True).start()
            response = redirect.wait(timeout=5)

        self.assertEqual("the-code", response["code"])
        self.assertEqual("the-state", response["state"])

    def test_it_binds_only_to_the_loopback_interface(self) -> None:
        with LoopbackRedirect() as redirect:
            self.assertTrue(redirect.redirect_uri.startswith("http://127.0.0.1:"))

    def test_a_provider_error_is_reported_instead_of_hanging(self) -> None:
        with LoopbackRedirect() as redirect:

            def visit() -> None:
                urllib.request.urlopen(
                    f"{redirect.redirect_uri}?error=access_denied", timeout=5
                ).read()

            threading.Thread(target=visit, daemon=True).start()
            response = redirect.wait(timeout=5)

        self.assertEqual("access_denied", response["error"])


if __name__ == "__main__":
    unittest.main()
