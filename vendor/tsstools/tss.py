"""Reader for Tektronix ``.tss`` session archives.

A ``.tss`` file is a plain ZIP container written by the TekScope application.
It holds one ``.wfm`` file per acquired channel, a ``manifest.json`` describing
the payload, a PNG screenshot of the instrument screen and a nested ``.set``
archive with the complete instrument setup in SCPI form::

    <name>.tss
      +- ch1.wfm ... ch8.wfm      Tektronix WFM#003 waveform data
      +- <name>.set               ZIP containing <name>_lrn.set (SCPI setup)
      +- <name>.png               instrument screen capture
      +- manifest.json            channel inventory + sha256 of every member

This module keeps the archive handling in one place so that the rest of the
package only ever sees decoded :class:`~tsstools.wfm003.WfmTrace` objects.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .wfm003 import WfmTrace, WfmFormatError, read_wfm

__all__ = ["TssArchive", "TssChannel", "TssReadError"]

_CHANNEL_RE = re.compile(r"^ch(\d+)\.wfm$", re.IGNORECASE)


class TssReadError(RuntimeError):
    """Raised when a ``.tss`` archive cannot be opened or decoded."""


@dataclass
class TssChannel:
    """One decoded channel of a ``.tss`` record.

    ``name`` is the archive member stem (``ch1`` ... ``ch8``); ``index`` is the
    numeric channel number used everywhere else in the package.
    """

    index: int
    name: str
    trace: WfmTrace

    @property
    def values(self) -> np.ndarray:
        return self.trace.values

    @property
    def units(self) -> str:
        return self.trace.units

    @property
    def sample_rate(self) -> float:
        return self.trace.sample_rate

    def stats(self) -> dict[str, float]:
        v = self.trace.values
        mean = float(v.mean())
        return {
            "mean": mean,
            "min": float(v.min()),
            "max": float(v.max()),
            "pp": float(v.max() - v.min()),
            "std": float(v.std()),
            "ac_rms": float(np.sqrt(np.mean((v - mean) ** 2))),
        }


@dataclass
class TssArchive:
    """Lazily-decoded view of one ``.tss`` archive."""

    path: Path
    _channels: dict[int, TssChannel] | None = field(default=None, repr=False)
    _manifest: dict | None = field(default=None, repr=False)
    _png: bytes | None = field(default=None, repr=False)
    _setup_bytes: bytes | None = field(default=None, repr=False)
    _errors: dict[str, str] = field(default_factory=dict, repr=False)

    # -- construction ------------------------------------------------------- #

    @classmethod
    def open(cls, path: str | Path) -> "TssArchive":
        path = Path(path)
        if not path.is_file():
            raise TssReadError(f"file not found: {path}")
        try:
            with zipfile.ZipFile(path) as zf:
                bad = zf.testzip()
                if bad is not None:
                    raise TssReadError(f"corrupt member {bad!r} in {path.name}")
        except zipfile.BadZipFile as exc:
            raise TssReadError(f"{path.name} is not a valid .tss (ZIP) archive") from exc
        return cls(path=path)

    # -- members ------------------------------------------------------------ #

    def member_names(self) -> list[str]:
        with zipfile.ZipFile(self.path) as zf:
            return zf.namelist()

    @property
    def manifest(self) -> dict:
        """Parsed ``manifest.json`` (empty dict when absent)."""
        if self._manifest is None:
            try:
                with zipfile.ZipFile(self.path) as zf:
                    self._manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            except (KeyError, ValueError, zipfile.BadZipFile):
                self._manifest = {}
        return self._manifest

    @property
    def screenshot_png(self) -> bytes | None:
        """Raw PNG bytes of the instrument screen capture, if present."""
        if self._png is None:
            try:
                with zipfile.ZipFile(self.path) as zf:
                    names = [n for n in zf.namelist() if n.lower().endswith(".png")]
                    self._png = zf.read(names[0]) if names else b""
            except (zipfile.BadZipFile, IndexError):
                self._png = b""
        return self._png or None

    @property
    def setup_bytes(self) -> bytes | None:
        """Raw bytes of the instrument setup (SCPI text, possibly ZIP-wrapped)."""
        if self._setup_bytes is None:
            try:
                with zipfile.ZipFile(self.path) as zf:
                    names = [n for n in zf.namelist() if n.lower().endswith(".set")]
                    raw = zf.read(names[0]) if names else b""
                # the .set is itself a ZIP holding <name>_lrn.set
                if raw[:2] == b"PK":
                    with zipfile.ZipFile(io.BytesIO(raw)) as inner:
                        members = inner.namelist()
                        raw = inner.read(members[0]) if members else b""
                self._setup_bytes = raw
            except (zipfile.BadZipFile, IndexError):
                self._setup_bytes = b""
        return self._setup_bytes or None

    # -- channels ----------------------------------------------------------- #

    def channel_indices(self) -> list[int]:
        """Channel numbers present in the archive, ascending."""
        with zipfile.ZipFile(self.path) as zf:
            found = []
            for name in zf.namelist():
                m = _CHANNEL_RE.match(Path(name).name)
                if m:
                    found.append(int(m.group(1)))
        return sorted(set(found))

    def read_channel(self, index: int) -> TssChannel:
        """Decode one channel by number (1-based)."""
        members = {Path(n).name.lower(): n for n in self.member_names()}
        key = f"ch{index}.wfm"
        if key not in members:
            raise TssReadError(f"{self.path.name}: channel {index} not present")
        with zipfile.ZipFile(self.path) as zf:
            payload = zf.read(members[key])
        try:
            trace = read_wfm(payload)
        except WfmFormatError as exc:
            raise TssReadError(f"{self.path.name}/{key}: {exc}") from exc
        return TssChannel(index=index, name=f"ch{index}", trace=trace)

    def read_channels(self, indices: list[int] | None = None) -> dict[int, TssChannel]:
        """Decode several channels, tolerating individual failures.

        Channels that fail to decode are recorded in :attr:`decode_errors`
        instead of aborting the whole file.
        """
        out: dict[int, TssChannel] = {}
        for idx in indices if indices is not None else self.channel_indices():
            try:
                out[idx] = self.read_channel(idx)
            except TssReadError as exc:
                self._errors[f"ch{idx}"] = str(exc)
        return out

    @property
    def decode_errors(self) -> dict[str, str]:
        """Channel number -> message for channels that failed to decode."""
        return dict(self._errors)

    # -- convenience -------------------------------------------------------- #

    def verify_against_manifest(self) -> list[str]:
        """Check member sizes against ``manifest.json``; returns problem strings."""
        problems: list[str] = []
        manifest = self.manifest
        if not manifest:
            return ["manifest.json missing"]
        entries = list(manifest.get("channelFiles", []))
        with zipfile.ZipFile(self.path) as zf:
            sizes = {Path(i.filename).name: i.file_size for i in zf.infolist()}
        for entry in entries:
            name = f"{entry.get('fileName', '')}{entry.get('fileType', '')}"
            if name not in sizes:
                problems.append(f"{name}: listed in manifest but missing from archive")
                continue
            expected = entry.get("fileSize")
            if expected is not None and sizes[name] != expected:
                problems.append(
                    f"{name}: size {sizes[name]} != manifest {expected}"
                )
        return problems
