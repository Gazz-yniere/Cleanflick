import struct

import pytest

from src import mediaduration


def _mp4_box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack('>I', 8 + len(payload)) + typ + payload


def _mp4_file(duration_seconds: float, timescale: int = 1000, version: int = 0) -> bytes:
    if version == 0:
        mvhd = struct.pack('>B3xIIII', 0, 0, 0, timescale, int(duration_seconds * timescale)) + b'\x00' * 80
    else:
        mvhd = struct.pack('>B3xQQIQ', 1, 0, 0, timescale, int(duration_seconds * timescale)) + b'\x00' * 80
    moov = _mp4_box(b'moov', _mp4_box(b'mvhd', mvhd))
    return b'\x00' * 16 + moov


def _ebml_vint(v: int) -> bytes:
    if v < 0x80:
        return bytes([0x80 | v])
    if v < 0x4000:
        return bytes([0x40 | (v >> 8), v & 0xFF])
    if v < 0x200000:
        return bytes([0x20 | (v >> 16), (v >> 8) & 0xFF, v & 0xFF])
    return bytes([0x10 | (v >> 24), (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF])


def _ebml_eid(e: int) -> bytes:
    # Octets réels (wire) des IDs EBML, bit de marque inclus
    return {
        0x8538067: b'\x18\x53\x80\x67',  # Segment
        0x0549A966: b'\x15\x49\xa9\x66',  # Info
        0x0AD7B1: b'\x2a\xd7\xb1',  # TimecodeScale
        0x489: b'\x44\x89',  # Duration
    }[e]


def _ebml_element(e: int, payload: bytes) -> bytes:
    return _ebml_eid(e) + _ebml_vint(len(payload)) + payload


def _mkv_file(duration_seconds: float, timecode_scale: int = 1_000_000) -> bytes:
    ebml_header = b'\x1A\x45\xDF\xA3\x84' + b'\x01\x01\x01\x01'
    # Duration est stockée en unités TimecodeScale (ms si scale=1e6), pas en secondes
    stored = duration_seconds * 1e9 / timecode_scale
    info_payload = _ebml_element(0x0AD7B1, timecode_scale.to_bytes(8, 'big')) + _ebml_element(0x489, struct.pack('>d', stored))
    info = _ebml_element(0x0549A966, info_payload)
    segment = _ebml_element(0x8538067, info)
    return ebml_header + segment


def test_mp4_duration_head(tmp_path):
    p = tmp_path / 'v.mp4'
    p.write_bytes(_mp4_file(3600.0))
    assert mediaduration.get_duration_seconds(str(p)) == pytest.approx(3600.0, rel=1e-6)


def test_mp4_duration_version1(tmp_path):
    p = tmp_path / 'v.mp4'
    p.write_bytes(_mp4_file(1800.0, version=1))
    assert mediaduration.get_duration_seconds(str(p)) == pytest.approx(1800.0, rel=1e-6)


def test_mp4_duration_at_tail(tmp_path):
    # moov en fin de fichier (mp4 optimisé streaming)
    p = tmp_path / 'v.mp4'
    p.write_bytes(b'\x00' * 4096 + _mp4_file(1200.0)[16:])
    assert mediaduration.get_duration_seconds(str(p)) == pytest.approx(1200.0, rel=1e-6)


def test_mkv_duration(tmp_path):
    p = tmp_path / 'v.mkv'
    p.write_bytes(_mkv_file(3723.4))
    assert mediaduration.get_duration_seconds(str(p)) == pytest.approx(3723.4, rel=1e-6)


def test_mkv_custom_timecode_scale(tmp_path):
    p = tmp_path / 'v.mkv'
    p.write_bytes(_mkv_file(10.0, timecode_scale=1_000))
    assert mediaduration.get_duration_seconds(str(p)) == pytest.approx(10.0, rel=1e-6)


def test_avi_duration(tmp_path):
    avih = struct.pack('<IIIIIIIIIIII', 1000, 0, 0, 0, 1800000, 0, 0, 0, 0, 0, 0, 0)
    hdrl = b'hdrl' + b'avih' + struct.pack('<I', len(avih)) + avih
    riff = b'RIFF' + struct.pack('<I', 4 + 8 + len(hdrl)) + b'AVI ' + b'LIST' + struct.pack('<I', len(hdrl)) + hdrl
    p = tmp_path / 'v.avi'
    p.write_bytes(riff)
    assert mediaduration.get_duration_seconds(str(p)) == pytest.approx(1800.0, rel=1e-6)


def test_unsupported_extension(tmp_path):
    p = tmp_path / 'v.flv'
    p.write_bytes(b'whatever')
    assert mediaduration.get_duration_seconds(str(p)) is None


def test_garbage_file(tmp_path):
    p = tmp_path / 'v.mp4'
    p.write_bytes(b'not a real video at all')
    assert mediaduration.get_duration_seconds(str(p)) is None


def test_empty_file(tmp_path):
    p = tmp_path / 'v.mp4'
    p.write_bytes(b'')
    assert mediaduration.get_duration_seconds(str(p)) is None


def test_missing_file(tmp_path):
    assert mediaduration.get_duration_seconds(str(tmp_path / 'absent.mp4')) is None


def test_minutes_rounding_and_bounds(tmp_path):
    p = tmp_path / 'v.mp4'
    p.write_bytes(_mp4_file(3725.0))
    assert mediaduration.get_duration_minutes(str(p)) == 62

    long_p = tmp_path / 'long.mp4'
    long_p.write_bytes(_mp4_file(601 * 60.0))
    assert mediaduration.get_duration_minutes(str(long_p)) is None
