"""Lecture de la durée d'un fichier vidéo en Python pur (sans ffprobe).

Compatible conteneur Docker (aucune dépendance externe). Ne lit que
l'en-tête du fichier, jamais l'intégralité en mémoire.
Supporte : MP4/M4V (box moov/mvhd), MKV/WebM (EBML Segment/Info/Duration),
AVI (RIFF hdrl/avih).
"""
import os
import struct


def _u32(b, off):
    return int.from_bytes(b[off:off + 4], 'big')


def _le32(b, off):
    return int.from_bytes(b[off:off + 4], 'little')


def _read_tail(f, fsize, head_size=1 << 20, tail_size=1 << 20):
    head = f.read(min(fsize, head_size))
    tail = b''
    if fsize > head_size:
        f.seek(fsize - tail_size)
        tail = f.read(tail_size)
    return head, tail


# ── MP4 / M4V ───────────────────────────────────────────────────────────────
def _mp4_from_buffer(buf):
    n = len(buf)
    i = 0
    while i + 8 <= n:
        size = _u32(buf, i)
        typ = buf[i + 4:i + 8]
        if size == 1:
            if i + 16 > n:
                return None
            size = int.from_bytes(buf[i + 8:i + 16], 'big')
            hdr = 16
        elif size == 0:
            size = n - i
            hdr = 8
        else:
            hdr = 8
        if typ == b'moov':
            return _parse_moov(buf, i + hdr, min(i + size, n))
        i += size
    return None


def _parse_moov(buf, start, end):
    i = start
    while i + 8 <= end:
        size = _u32(buf, i)
        typ = buf[i + 4:i + 8]
        if size == 1:
            if i + 16 > end:
                return None
            size = int.from_bytes(buf[i + 8:i + 16], 'big')
            hdr = 16
        elif size == 0:
            size = end - i
            hdr = 8
        else:
            hdr = 8
        if typ == b'mvhd':
            return _parse_mvhd(buf, i + hdr, min(i + size, end))
        i += size
    return None


def _parse_mvhd(buf, start, end):
    if start >= end:
        return None
    version = buf[start]
    if version == 1:
        ts = _u32(buf, start + 20)
        dur = int.from_bytes(buf[start + 24:start + 32], 'big')
    else:
        ts = _u32(buf, start + 12)
        dur = _u32(buf, start + 16)
    if ts and dur:
        return dur / ts
    return None


def _mp4_backward(path, fsize, window=8 << 20):
    """Remonte les boxes depuis la fin du fichier (moov en fin, mp4 streaming)."""
    with open(path, 'rb') as f:
        pos = fsize - 8
        while pos >= 0 and fsize - pos <= window:
            f.seek(pos)
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            size = int.from_bytes(hdr[0:4], 'big')
            typ = hdr[4:8]
            if 8 <= size <= fsize - pos:
                if typ == b'moov':
                    f.seek(pos)
                    moov = f.read(size)
                    d = _parse_moov(moov, 8, size)
                    if d is not None:
                        return d
                pos -= size
            else:
                pos -= 8
    return None


def _read_mp4(path, fsize):
    with open(path, 'rb') as f:
        head = f.read(min(fsize, 1 << 20))
    d = _mp4_from_buffer(head)
    if d is not None:
        return d
    # moov probablement en fin de fichier (mp4 optimisé streaming).
    tail_size = min(8 << 20, fsize)
    with open(path, 'rb') as f:
        f.seek(fsize - tail_size)
        tail = f.read(tail_size)
    idx = 0
    while True:
        i = tail.find(b'moov', idx)
        if i < 0:
            break
        if i >= 4:
            size = int.from_bytes(tail[i - 4:i], 'big')
            if 8 <= size <= tail_size and i - 4 + size <= len(tail):
                d = _parse_moov(tail, i + 4, i - 4 + size)
                if d is not None:
                    return d
        idx = i + 1
    return _mp4_backward(path, fsize)


# ── MKV / WebM (EBML) ───────────────────────────────────────────────────────
def _ebml_read_vint(b, off):
    lead = b[off]
    mask = 0x80
    length = 1
    while mask and not (lead & mask):
        mask >>= 1
        length += 1
    value = lead & (mask - 1)
    for k in range(1, length):
        value = (value << 8) | b[off + k]
    return value, length


def _mkv_from_buffer(buf):
    n = len(buf)
    # Recherche de l'élément Segment (0x18538067)
    i = 0
    while i < n:
        try:
            eid, elen = _ebml_read_vint(buf, i)
            size, slen = _ebml_read_vint(buf, i + elen)
        except Exception:
            return (None, 1000000)
        hdrlen = elen + slen
        if eid == 0x18538067:
            return _parse_segment(buf, i + hdrlen, min(n, i + hdrlen + size) if size < (1 << 50) else n)
        if size >= (1 << 50):
            return (None, 1000000)
        i += hdrlen + size
    return (None, 1000000)


def _parse_segment(buf, start, end):
    timecode_scale = 1000000
    duration = None
    i = start
    while i < end:
        try:
            eid, elen = _ebml_read_vint(buf, i)
            size, slen = _ebml_read_vint(buf, i + elen)
        except Exception:
            break
        hdrlen = elen + slen
        data_start = i + hdrlen
        data_end = data_start + size
        if data_end > len(buf):
            break
        if eid == 0x1549A966:  # Info
            j = data_start
            while j < data_end:
                try:
                    feid, fel = _ebml_read_vint(buf, j)
                    fsize, fsl = _ebml_read_vint(buf, j + fel)
                except Exception:
                    break
                fhdr = fel + fsl
                fdata = j + fhdr
                if feid == 0x2AD7B1:  # TimecodeScale
                    v = int.from_bytes(buf[fdata:fdata + fsize], 'big')
                    if v:
                        timecode_scale = v
                elif feid == 0x4489:  # Duration (float secondes, souvent 8 octets)
                    if fsize == 8:
                        duration = struct.unpack('>d', buf[fdata:fdata + 8])[0]
                    elif fsize == 4:
                        duration = struct.unpack('>f', buf[fdata:fdata + 4])[0]
                    else:
                        duration = int.from_bytes(buf[fdata:fdata + fsize], 'big')
                j = fdata + fsize
            return (duration, timecode_scale)
        if size == 0:
            break
        i = data_end
    return (None, timecode_scale)


def _mkv_last_cluster_timecode(tail):
    """Timecode (unités TimecodeScale) du dernier cluster, pour les MKV sans Duration."""
    last = -1
    idx = 0
    while True:
        i = tail.find(b'\x1f\x43\xb6\x75', idx)  # Cluster
        if i < 0:
            break
        last = i
        idx = i + 1
    if last < 0:
        return None
    n = len(tail)
    i = last + 4
    # sauter le vint de taille du cluster
    try:
        _, slen = _ebml_read_vint(tail, i)
        i += slen
    except Exception:
        return None
    while i + 2 <= n:
        try:
            eid, elen = _ebml_read_vint(tail, i)
            size, szlen = _ebml_read_vint(tail, i + elen)
        except Exception:
            break
        hdrlen = elen + szlen
        if eid == 0xE7 and 0 < size <= 8:  # Timecode
            return int.from_bytes(tail[i + hdrlen:i + hdrlen + size], 'big')
        i += hdrlen + size
    return None


def _read_mkv(path, fsize):
    with open(path, 'rb') as f:
        head, _ = _read_tail(f, fsize)
    duration, tc_scale = _mkv_from_buffer(head)
    if duration:
        return duration * tc_scale / 1e9
    # Repli : dernier cluster (la durée n'est pas stockée dans l'en-tête).
    tail_size = min(4 << 20, fsize)
    with open(path, 'rb') as f:
        f.seek(fsize - tail_size)
        tail = f.read(tail_size)
    tc = _mkv_last_cluster_timecode(tail)
    if tc:
        return tc * tc_scale / 1e9
    return None


# ── AVI (RIFF) ───────────────────────────────────────────────────────────────
def _parse_avih(buf, off):
    micro = _le32(buf, off)
    total = _le32(buf, off + 16)
    if micro and total:
        return total * micro / 1e6
    return None


def _avi_from_buffer(buf):
    n = len(buf)
    if n < 12 or buf[0:4] != b'RIFF' or buf[8:12] != b'AVI ':
        return None
    i = 12
    while i + 8 <= n:
        fourcc = buf[i:i + 4]
        size = _le32(buf, i + 4)
        data = i + 8
        if fourcc == b'LIST':
            if size == 0 or size >= (1 << 30):
                break
            lst = buf[data:data + 4] if data + 4 <= n else b''
            if lst == b'hdrl':
                j = data + 4
                hend = min(n, data + 4 + size)
                while j + 8 <= hend:
                    cfourcc = buf[j:j + 4]
                    csize = _le32(buf, j + 4)
                    if cfourcc == b'avih':
                        return _parse_avih(buf, j + 8)
                    j += 8 + csize + (csize & 1)
                return None
            i = data + 4 + size + (size & 1)
            continue
        if fourcc == b'movi' or size == 0 or size >= (1 << 30):
            break
        i = data + size + (size & 1)
    return None


def _read_avi(path, fsize):
    with open(path, 'rb') as f:
        head, _ = _read_tail(f, fsize, tail_size=1 << 16)
    return _avi_from_buffer(head)


# ── API publique ─────────────────────────────────────────────────────────────
def get_duration_seconds(path):
    """Renvoie la durée en secondes (float) ou None si non lisible."""
    try:
        fsize = os.path.getsize(path)
        if fsize <= 0:
            return None
        ext = os.path.splitext(path)[1].lower()
        if ext in ('.mp4', '.m4v', '.mov', '.ts'):
            return _read_mp4(path, fsize)
        if ext in ('.mkv', '.webm'):
            return _read_mkv(path, fsize)
        if ext in ('.avi',):
            return _read_avi(path, fsize)
    except Exception:
        return None
    return None


def get_duration_minutes(path):
    s = get_duration_seconds(path)
    if not s or s <= 0:
        return None
    minutes = int(round(s / 60.0))
    # Borne de sécurité : au-delà de 10 h, considérer le parse comme invalide.
    if minutes <= 0 or minutes > 600:
        return None
    return minutes