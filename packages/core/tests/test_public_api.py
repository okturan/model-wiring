from __future__ import annotations

import unittest

import model_wiring

# What this change promises an integrating application: a broker, a gateway, and
# the posture vocabulary that decides which login drivers a route may offer.
ADDED_EXPORTS = frozenset(
    {
        "CredentialBroker",
        "CredentialResolution",
        "CredentialSnapshot",
        "DEFAULT_TERMS_POSTURE",
        "GatewayRecord",
        "GatewayRoute",
        "InferenceGateway",
        "RuntimeCredential",
        "TERMS_POSTURES",
        "THIRD_PARTY_CLIENT_DRIVERS",
        "gateway_probe_driver",
        "gateway_probe_drivers",
        "gateway_routes",
        "gateway_server",
        "make_gateway_handler",
        "permits_third_party_client",
        "permitted_drivers",
        "provider_gateway_routes",
        "serve_gateway",
    }
)


def _sort_key(name: str) -> tuple[int, str]:
    """Constants, then classes, then functions: the order the package keeps."""

    if name.isupper():
        return 0, name
    if name[0].isupper():
        return 1, name
    return 2, name


class PublicApiTests(unittest.TestCase):
    def test_every_exported_name_exists(self) -> None:
        missing = [
            name for name in model_wiring.__all__ if not hasattr(model_wiring, name)
        ]

        self.assertEqual([], missing)

    def test_the_export_list_is_sorted_and_has_no_duplicates(self) -> None:
        names = list(model_wiring.__all__)

        self.assertEqual(sorted(names, key=_sort_key), names)
        self.assertEqual(len(set(names)), len(names))

    def test_the_new_capabilities_are_reachable_from_the_package(self) -> None:
        self.assertEqual(set(), ADDED_EXPORTS - set(model_wiring.__all__))

    def test_the_gateway_and_broker_come_from_their_own_modules(self) -> None:
        self.assertIs(
            model_wiring.InferenceGateway, model_wiring.gateway.InferenceGateway
        )
        self.assertIs(
            model_wiring.CredentialBroker, model_wiring.broker.CredentialBroker
        )


if __name__ == "__main__":
    unittest.main()
