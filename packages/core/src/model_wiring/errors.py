"""Public exception hierarchy for model-wiring."""

from __future__ import annotations


class ModelProviderError(Exception):
    """Base class for expected kit failures."""


class CatalogError(ModelProviderError):
    """The provider/model catalog is unavailable or invalid."""


class CatalogUnavailable(CatalogError):
    """No fresh remote or cached catalog could be loaded."""


class SelectionError(ModelProviderError):
    """A selection intent cannot be resolved safely."""


class SelectionNotFound(SelectionError):
    """No provider/model matches the intent."""


class AmbiguousSelection(SelectionError):
    """More than one candidate matches and guessing would be unsafe."""

    def __init__(self, message: str, candidates: tuple[str, ...]) -> None:
        super().__init__(message)
        self.candidates = candidates


class IncompatibleSelection(SelectionError):
    """A known model does not satisfy a hard requirement."""


class ProfileError(ModelProviderError):
    """Credential profile metadata is invalid or unavailable."""


class CredentialError(ModelProviderError):
    """Credential material cannot be acquired safely."""


class SecretNotFound(CredentialError):
    """The referenced secret does not exist in its configured store."""


class RefreshError(CredentialError):
    """An OAuth credential could not be refreshed."""
