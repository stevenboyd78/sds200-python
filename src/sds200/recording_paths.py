from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from string import Formatter

from .recording_identity import RecordingIdentity
from .recording_metadata import recording_metadata_path
from .recording_organization import RecordingOrganizationPolicy

DEFAULT_RECORDING_TEMPLATE = "sds200-{timestamp}.wav"


@dataclass(frozen=True, slots=True)
class RecordingPathPolicy:
    """Resolve explicit or collision-safe organized recording paths."""

    output: Path | None = None
    directory: Path | None = None
    template: str = DEFAULT_RECORDING_TEMPLATE
    overwrite: bool = False
    organization: RecordingOrganizationPolicy = field(
        default_factory=RecordingOrganizationPolicy
    )

    def __post_init__(self) -> None:
        if self.output is not None and self.directory is not None:
            raise ValueError("Audio output and audio directory are mutually exclusive")
        if self.organization.enabled and self.directory is None:
            raise ValueError("Audio organization requires an audio directory")
        if not self.template.strip():
            raise ValueError("Audio filename template must not be empty")
        try:
            fields = tuple(Formatter().parse(self.template))
        except ValueError as error:
            raise ValueError(
                "Audio filename template must include only the {timestamp} field"
            ) from error
        replacements = tuple(
            field for _literal, field, _spec, _conversion in fields if field
        )
        if not replacements or any(
            field != "timestamp" or bool(spec) or conversion is not None
            for _literal, field, spec, conversion in fields
            if field is not None
        ):
            raise ValueError(
                "Audio filename template must include only the {timestamp} field"
            )
        try:
            rendered = self.template.format(timestamp="20000101-000000")
        except (KeyError, IndexError, ValueError) as error:
            raise ValueError(
                "Audio filename template must include only the {timestamp} field"
            ) from error
        if Path(rendered).is_absolute() or Path(rendered).name != rendered:
            raise ValueError("Audio filename template must produce a file name")
        if not rendered.casefold().endswith(".wav"):
            raise ValueError("Audio filename template must produce a .wav file")

    @property
    def enabled(self) -> bool:
        return self.output is not None or self.directory is not None

    @property
    def display_path(self) -> Path:
        if self.output is not None:
            return self.output.expanduser()
        if self.directory is not None:
            return self.directory.expanduser()
        return Path("-")

    @property
    def repeatable(self) -> bool:
        return self.directory is not None

    def next_path(
        self,
        now: datetime,
        *,
        explicit_used: bool,
        metadata: bool = False,
        identity: RecordingIdentity | None = None,
    ) -> Path:
        def available(candidate: Path) -> bool:
            return not candidate.exists() and (
                not metadata or not recording_metadata_path(candidate).exists()
            )

        if self.output is not None:
            if explicit_used:
                raise RuntimeError(
                    "The explicit audio output has already been used; "
                    "use a recording directory for repeatable recordings"
                )
            output = self.output.expanduser()
            sidecar = recording_metadata_path(output)
            if metadata and not self.overwrite and sidecar.exists():
                raise FileExistsError(f"Recording metadata already exists: {sidecar}")
            return output
        if self.directory is None:
            raise RuntimeError("Audio recording is not configured")

        directory = self.directory.expanduser()
        if self.organization.enabled:
            if identity is None:
                raise RuntimeError(
                    "Recording organization requires a start-boundary identity"
                )
            directory = directory / self.organization.relative_directory(identity)
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = now.strftime("%Y%m%d-%H%M%S")
        name = self.template.format(timestamp=timestamp)
        candidate = directory / name
        if available(candidate):
            return candidate
        for sequence in range(2, 10_000):
            suffixed = candidate.with_name(
                f"{candidate.stem}-{sequence}{candidate.suffix}"
            )
            if available(suffixed):
                return suffixed
        raise RuntimeError("Could not allocate a collision-safe audio filename")

    def library_paths(self) -> tuple[Path, ...]:
        if self.directory is not None:
            directory = self.directory.expanduser()
            if not directory.exists():
                return ()
            return tuple(directory.rglob("*.wav"))
        if self.output is not None:
            output = self.output.expanduser()
            return (output,) if output.exists() else ()
        return ()
