"""Pure-Python reader for Tektronix ``WFM#003`` waveform files.

The Tektronix 4/5/6-series MSO instruments (and the TekScope desktop
application) store each acquired channel as a ``.wfm`` file.  Inside a ``.tss``
archive those files are named ``ch1.wfm`` ... ``ch8.wfm``.

Three on-disk layouts exist (``:WFM#001``, ``:WFM#002``, ``:WFM#003``).  The
files in this dataset are all ``:WFM#003``.  This module implements a
dependency-free decoder for that layout, following the struct layout published
by Tektronix in the ``tm_data_types`` package (Apache-2.0).  The byte layout was
additionally verified against every file in this dataset by asserting that the
``byte_offset`` field of the static file-info block equals the size of the
header computed from the recorded dimension counts.

Layout of a version-3 file::

    offset 0   : 2 bytes  byte-order marker  (0f0f = big-endian/PPC,
                                             f0f0 = little-endian/Intel)
    offset 2   : 8 bytes  version string    (":WFM#003")
    offset 10  : WaveformStaticFileInfo     (68 bytes)
    +68        : WaveformHeader             (76 bytes)
    +76        : summary frame type         (2 bytes, v2/v3 only)
    +2         : PixMap                     (12 bytes)
    +12        : explicit dimension  #1     (100 bytes) + user view #1 (60 bytes)
               : explicit dimension  #2     (100 bytes) + user view #2 (60 bytes)
               : implicit dimension  #1     ( 76 bytes) + user view #1 (60 bytes)
               : implicit dimension  #2     ( 76 bytes) + user view #2 (60 bytes)
               : TimeBaseInformation #1     (12 bytes)
               : TimeBaseInformation #2     (12 bytes)
               : UpdateSpecifications       (24 bytes)
               : CurveInformation           (30 bytes)
               : number_of_frames x UpdateSpecifications
               : number_of_frames x CurveInformation
               : precharge | curve | postcharge sample buffers
               : file checksum (uint64, optional)
               : "tekmeta!" metadata block

Scaling of the sample data follows the Tektronix convention::

    value = raw_sample * explicit_dimension.scale + explicit_dimension.offset

where ``explicit_dimension.scale`` is expressed in ``units`` per ADC LSB, and
``units`` is the physical unit of the channel (``V``, ``A``, ``W`` ...).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

import numpy as np

__all__ = ["WfmFormatError", "WfmTrace", "read_wfm", "WFM003_HEADER_BYTES"]

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

_BYTE_ORDER_TO_STRUCT = {
    b"\x0f\x0f": "<",  # PPC / big-endian payload, little-endian struct prefix
    b"\xf0\xf0": ">",  # Intel / little-endian payload
}

_VERSION_STRINGS = {
    b":WFM#001": 1,
    b":WFM#002": 2,
    b":WFM#003": 3,
}

# wave-dimension data formats (CurveFormatsVer3)
_CURVE_DTYPES = {
    0: "i2",   # EXPLICIT_INT16
    1: "i4",   # EXPLICIT_INT32
    2: "u4",   # EXPLICIT_UINT32
    3: "u8",   # EXPLICIT_UINT64
    4: "f4",   # EXPLICIT_FP32
    5: "f8",   # EXPLICIT_FP64
    6: "u1",   # EXPLICIT_UINT8
    7: "i1",   # EXPLICIT_INT8
}

# struct formats (all fields are packed, no alignment: the struct prefix is
# always explicit, which forces standard sizes and suppresses padding)
_FMT_FILE_INFO = "<BLBllfdf32sLH"
_FMT_HEADER = "<iLQQiiLLLiQLLLLL"
_FMT_SUMMARY = "<H"
_FMT_PIXMAP = "<iQ"
_FMT_EXPLICIT_DIM = "<ddL20sddddiiiiiii"
_FMT_IMPLICIT_DIM = "<ddL20sddddL"
_FMT_USER_VIEW_V3 = "<d20sdddd"
_FMT_USER_VIEW_V12 = "<d20sLdd"
_FMT_TIME_BASE = "<Lii"
_FMT_UPDATE_SPEC = "<Lddl"
_FMT_CURVE_INFO = "<LihLLLLL"

#: Number of bytes a WFM#003 header occupies when a single trace and no extra
#: FastFrame blocks are present (10 + 68 + 76 + 2 + 12 + 320 + 272 + 24 + 24 + 30).
WFM003_HEADER_BYTES = 838


class WfmFormatError(RuntimeError):
    """Raised when a byte stream is not a readable Tektronix waveform file."""


# --------------------------------------------------------------------------- #
# Data containers
# --------------------------------------------------------------------------- #


@dataclass
class WfmTrace:
    """One decoded analog channel.

    Attributes:
        values: Scaled sample values in ``units`` (float64).
        raw: Raw integer/float samples exactly as stored in the file.
        dt: Sample interval in seconds (1 / sample rate).
        t0: Time of sample 0, in seconds, relative to the trigger instant.
        trigger_index: Index of the trigger sample inside the record.
        units: Physical unit of ``values`` (``V``/``A``/``W``/...).
        record_length: Number of acquired samples.
        scale: Volts (or amps) per LSB.
        offset: Value of raw sample 0.
        sample_rate: Sampling rate in Hz.
        extra: Any additional header information worth keeping.
    """

    values: np.ndarray
    raw: np.ndarray
    dt: float
    t0: float
    trigger_index: float
    units: str
    record_length: int
    scale: float
    offset: float
    sample_rate: float
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def time_axis(self) -> np.ndarray:
        """Trigger-referenced time axis in seconds."""
        return self.t0 + np.arange(self.record_length, dtype=np.float64) * self.dt


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _unpack(fmt: str, buf: bytes | memoryview, offset: int) -> tuple[tuple, int]:
    size = struct.calcsize(fmt)
    if offset + size > len(buf):
        raise WfmFormatError(
            f"truncated waveform header: need {size} bytes at offset {offset}, "
            f"buffer holds {len(buf)}"
        )
    return struct.unpack_from(fmt, buf, offset), offset + size


def _decode_text(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def read_wfm(data: bytes) -> WfmTrace:
    """Decode a single ``.wfm`` byte string into a :class:`WfmTrace`.

    Args:
        data: Complete contents of a ``.wfm`` file.

    Returns:
        The decoded trace, scaled to physical units.

    Raises:
        WfmFormatError: If the stream is not a supported Tektronix waveform.
    """
    if len(data) < WFM003_HEADER_BYTES:
        raise WfmFormatError(f"file too short for a .wfm header ({len(data)} bytes)")

    order = bytes(data[0:2])
    if order not in _BYTE_ORDER_TO_STRUCT:
        raise WfmFormatError(
            f"unrecognised byte-order marker {order!r}; not a Tektronix .wfm file"
        )
    prefix = _BYTE_ORDER_TO_STRUCT[order]

    version_raw = bytes(data[2:10])
    version = _VERSION_STRINGS.get(version_raw)
    if version is None:
        raise WfmFormatError(f"unsupported Tektronix waveform version {version_raw!r}")
    if version != 3:
        raise WfmFormatError(
            f"only WFM#003 is implemented, file reports {version_raw.decode()!r}"
        )

    # ---- static file info ------------------------------------------------- #
    (file_info, off) = _unpack(_FMT_FILE_INFO, data, 10)
    (
        digits_in_byte_count,
        bytes_till_eof,
        bytes_per_point,
        byte_offset,
        h_zoom_scale,
        h_zoom_pos,
        v_zoom_scale,
        v_zoom_pos,
        waveform_label,
        number_of_frames,
        header_size,
    ) = file_info
    del digits_in_byte_count, h_zoom_scale, h_zoom_pos, v_zoom_pos, header_size

    # ---- wave header ------------------------------------------------------ #
    (header, off) = _unpack(_FMT_HEADER, data, off)
    (
        waveform_type,
        wfm_count,
        acquisition_counter,
        transaction_stamp,
        slot_id,
        is_static,
        update_spec_cnt,
        imp_dim_ref_cnt,
        exp_dim_ref_cnt,
        data_type,
        gen_purpose_counter,
        accumulate_wfm_cnt,
        target_accumulation_cnt,
        curve_ref_cnt,
        num_requested_fast_frames,
        num_acquired_fast_frames,
    ) = header
    del acquisition_counter, transaction_stamp, slot_id, is_static
    del gen_purpose_counter, accumulate_wfm_cnt, target_accumulation_cnt
    del num_acquired_fast_frames

    (summary_frame_type,), off = _unpack(_FMT_SUMMARY, data, off)
    (_pixmap_format, _pixmap_max), off = _unpack(_FMT_PIXMAP, data, off)

    # ---- dimensions ------------------------------------------------------- #
    def read_dim(fmt_dim: str) -> tuple[tuple, tuple]:
        nonlocal off
        dim, off2 = _unpack(fmt_dim, data, off)
        view, off3 = _unpack(_FMT_USER_VIEW_V3, data, off2)
        off = off3
        return dim, view

    explicit, _explicit_view = read_dim(_FMT_EXPLICIT_DIM)
    read_dim(_FMT_EXPLICIT_DIM)  # second explicit dimension (unused)
    implicit, _implicit_view = read_dim(_FMT_IMPLICIT_DIM)
    read_dim(_FMT_IMPLICIT_DIM)  # second implicit dimension (unused)

    (
        ex_scale,
        ex_offset,
        ex_size,
        ex_units,
        ex_extent_min,
        ex_extent_max,
        ex_resolution,
        ex_reference_point,
        ex_format,
        ex_storage_type,
        ex_null_value,
        ex_over_range,
        ex_under_range,
        ex_high_range,
        ex_low_range,
    ) = explicit

    (
        im_scale,
        im_offset,
        im_size,
        im_units,
        im_extent_min,
        im_extent_max,
        im_resolution,
        im_reference_point,
        im_spacing,
    ) = implicit

    time_base_1, off = _unpack(_FMT_TIME_BASE, data, off)
    _unpack(_FMT_TIME_BASE, data, off)  # second timebase entry (unused)
    off += struct.calcsize(_FMT_TIME_BASE)

    update_spec, off = _unpack(_FMT_UPDATE_SPEC, data, off)
    real_point_offset, trigger_time_offset, fractional_second, gmt_second = update_spec

    curve_info, off = _unpack(_FMT_CURVE_INFO, data, off)
    (
        state_flags,
        check_sum_type,
        check_sum,
        precharge_start_offset,
        data_start_offset,
        postcharge_start_offset,
        postcharge_stop_offset,
        end_of_curve_buffer_offset,
    ) = curve_info

    # FastFrame additions
    extra_specs = []
    for _ in range(int(number_of_frames)):
        spec, off = _unpack(_FMT_UPDATE_SPEC, data, off)
        extra_specs.append(spec)
    for _ in range(int(number_of_frames)):
        _unpack(_FMT_CURVE_INFO, data, off)
        off += struct.calcsize(_FMT_CURVE_INFO)

    # ---- sanity check the computed header size ---------------------------- #
    if off != int(byte_offset):
        raise WfmFormatError(
            "waveform header size mismatch: parsed "
            f"{off} bytes but the file declares the curve buffer starts at "
            f"{int(byte_offset)}; the file may use an unsupported variant"
        )

    # ---- curve buffers ---------------------------------------------------- #
    dtype_code = int(ex_format)
    if dtype_code not in _CURVE_DTYPES:
        raise WfmFormatError(
            f"unsupported curve sample format {dtype_code} "
            "(EXPLICIT_NO_DIMENSION / INVALID)"
        )
    np_dtype = np.dtype(prefix + _CURVE_DTYPES[dtype_code])

    if int(bytes_per_point) != np_dtype.itemsize:
        raise WfmFormatError(
            f"bytes_per_point ({bytes_per_point}) disagrees with the declared "
            f"sample format ({np_dtype.itemsize} bytes)"
        )

    def buffer_length(start: int, stop: int) -> int:
        return (stop - start) // int(bytes_per_point)

    precharge_len = buffer_length(precharge_start_offset, data_start_offset)
    curve_len = buffer_length(data_start_offset, postcharge_start_offset)
    postcharge_len = buffer_length(postcharge_start_offset, postcharge_stop_offset)

    if curve_len <= 0:
        raise WfmFormatError("waveform declares an empty curve buffer")

    nbytes = (precharge_len + curve_len + postcharge_len) * np_dtype.itemsize
    if off + nbytes > len(data):
        raise WfmFormatError(
            f"curve buffer runs past end of file "
            f"(need {nbytes} bytes at {off}, file has {len(data)})"
        )

    samples = np.frombuffer(
        data, dtype=np_dtype, count=precharge_len + curve_len + postcharge_len, offset=off
    )
    curve_raw = np.ascontiguousarray(samples[precharge_len : precharge_len + curve_len])

    # ---- time base -------------------------------------------------------- #
    dt = float(im_scale)
    if not np.isfinite(dt) or dt <= 0.0:
        raise WfmFormatError(f"invalid implicit-dimension scale (sample interval) {dt!r}")
    trigger_index = -float(im_offset) / dt

    # record-length cross checks
    if int(im_size) not in (0, curve_len):
        # im_size is the record length; allow zero for exotic files but flag mismatch
        if int(im_size) != curve_len:
            raise WfmFormatError(
                f"record length mismatch: header says {int(im_size)} samples, "
                f"curve buffer holds {curve_len}"
            )

    values = curve_raw.astype(np.float64) * float(ex_scale) + float(ex_offset)

    return WfmTrace(
        values=values,
        raw=curve_raw,
        dt=dt,
        t0=-trigger_index * dt,
        trigger_index=trigger_index,
        units=_decode_text(ex_units),
        record_length=int(curve_len),
        scale=float(ex_scale),
        offset=float(ex_offset),
        sample_rate=1.0 / dt,
        extra={
            "version": 3,
            "waveform_type": int(waveform_type),
            "wfm_count": int(wfm_count),
            "update_spec_cnt": int(update_spec_cnt),
            "imp_dim_ref_cnt": int(imp_dim_ref_cnt),
            "exp_dim_ref_cnt": int(exp_dim_ref_cnt),
            "data_type": int(data_type),
            "curve_ref_cnt": int(curve_ref_cnt),
            "number_of_frames": int(number_of_frames),
            "num_requested_fast_frames": int(num_requested_fast_frames),
            "summary_frame_type": int(summary_frame_type),
            "waveform_label": _decode_text(waveform_label),
            "curve_format": dtype_code,
            "explicit_size": int(ex_size),
            "explicit_units": _decode_text(ex_units),
            "explicit_extent_min": float(ex_extent_min),
            "explicit_extent_max": float(ex_extent_max),
            "explicit_reference_point": float(ex_reference_point),
            "explicit_storage_type": int(ex_storage_type),
            "explicit_null_value": int(ex_null_value),
            "explicit_over_range": int(ex_over_range),
            "explicit_under_range": int(ex_under_range),
            "explicit_high_range": int(ex_high_range),
            "explicit_low_range": int(ex_low_range),
            "implicit_units": _decode_text(im_units),
            "implicit_size": int(im_size),
            "implicit_offset": float(im_offset),
            "implicit_extent_min": float(im_extent_min),
            "implicit_extent_max": float(im_extent_max),
            "implicit_resolution": float(im_resolution),
            "implicit_reference_point": float(im_reference_point),
            "implicit_spacing": int(im_spacing),
            "real_point_spacing": int(time_base_1[0]),
            "sweep": int(time_base_1[1]),
            "type_of_base": int(time_base_1[2]),
            "real_point_offset": int(real_point_offset),
            "trigger_time_offset": float(trigger_time_offset),
            "fractional_second": float(fractional_second),
            "gmt_second": int(gmt_second),
            "state_flags": int(state_flags),
            "check_sum_type": int(check_sum_type),
            "check_sum": int(check_sum),
            "end_of_curve_buffer_offset": int(end_of_curve_buffer_offset),
            "bytes_till_eof": int(bytes_till_eof),
            "header_size": int(off),
            "fastframe_specs": len(extra_specs),
        },
    )
