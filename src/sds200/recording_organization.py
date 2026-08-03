from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .recording_identity import (
    DEFAULT_RECORDING_COMPONENT,
    MAX_RECORDING_COMPONENT_LENGTH,
    RecordingIdentity,
    safe_recording_component,
)

RECORDING_ORGANIZATION_COMPONENTS: Final[tuple[str, ...]] = (
    "scanner",
    "date",
    "system",
    "department",
    "site",
    "channel",
)


@dataclass(frozen=True, slots=True)
class RecordingOrganizationPolicy:
    """Render ordered safe relative directories from recording identity."""

    components: tuple[str, ...] = ()
    fallback: str = DEFAULT_RECORDING_COMPONENT
    max_component_length: int = MAX_RECORDING_COMPONENT_LENGTH

    def __post_init__(self) -> None:
        if isinstance(self.max_component_length, bool) or self.max_component_length < 1:
            raise ValueError(
                "Recording organization component length must be positive."
            )
        safe_recording_component(
            None,
            fallback=self.fallback,
            max_length=self.max_component_length,
        )

        normalized = tuple(component.strip() for component in self.components)
        if any(not component for component in normalized):
            raise ValueError("Recording organization components must not be empty.")

        unsupported = tuple(
            component
            for component in normalized
            if component not in RECORDING_ORGANIZATION_COMPONENTS
        )
        if unsupported:
            supported = ", ".join(RECORDING_ORGANIZATION_COMPONENTS)
            raise ValueError(
                f"Unsupported recording organization component: {unsupported[0]}. "
                f"Supported components: {supported}."
            )

        seen: set[str] = set()
        for component in normalized:
            if component in seen:
                raise ValueError(
                    f"Duplicate recording organization component: {component}."
                )
            seen.add(component)

        object.__setattr__(self, "components", normalized)

    @classmethod
    def from_csv(cls, value: str) -> RecordingOrganizationPolicy:
        """Parse one ordered comma-separated organization policy."""

        if not value.strip():
            raise ValueError("Recording organization components must not be empty.")
        return cls(tuple(value.split(",")))

    @property
    def enabled(self) -> bool:
        return bool(self.components)

    def relative_directory(self, identity: RecordingIdentity) -> Path:
        """Return a safe relative directory without creating it."""

        if not self.components:
            return Path()

        values = identity.filename_components(
            fallback=self.fallback,
            max_length=self.max_component_length,
        )
        directory = Path(*(values[component] for component in self.components))
        if directory.is_absolute() or any(
            part in {"", ".", ".."} for part in directory.parts
        ):
            raise RuntimeError(
                "Recording organization produced an unsafe relative directory."
            )
        return directory
