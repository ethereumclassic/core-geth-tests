"""Keccak-256, the hash Ethereum calls `sha3` and `keccak256`, in Python's standard library alone.

Keccak-256 is the sponge over the Keccak-f[1600] permutation with a rate of 1088 bits, padded with
the original Keccak rule: the byte 0x01 after the message, then zeros, then 0x80 in the block's last
byte. FIPS 202's SHA3-256 uses the same permutation and rate but pads with 0x06, so `hashlib.sha3_256`
returns different digests. `sha3_256` here runs this module's sponge with that padding, so the tests
can check the permutation against `hashlib` on any input.

`permute` is a straightforward transcription of FIPS 202, section 3.2. `_sponge` inlines the same
round, unrolled, because it is about twice as fast; the tests check one against the other.
"""

import struct

RATE = 136  # bytes absorbed per block: 1600 bits of state less a 512-bit capacity
_M = (1 << 64) - 1


def _round_constants():
    """The 24 round constants of the iota step, from FIPS 202's LFSR rc(t) (Algorithm 5)."""
    constants = []
    r = 1
    for _ in range(24):
        c = 0
        for j in range(7):
            if r & 1:
                c |= 1 << ((1 << j) - 1)
            r = ((r << 1) ^ 0x71) & 0xFF if r & 0x80 else (r << 1) & 0xFF
        constants.append(c)
    return tuple(constants)


def _rotation_offsets():
    """The rho step's offsets, ROT[x][y], walked as FIPS 202's Algorithm 2 walks them."""
    rot = [[0] * 5 for _ in range(5)]
    x, y = 1, 0
    for t in range(24):
        rot[x][y] = ((t + 1) * (t + 2) // 2) % 64
        x, y = y, (2 * x + 3 * y) % 5
    return rot


RC = _round_constants()
ROT = _rotation_offsets()


def permute(lanes):
    """Keccak-f[1600] in place on 25 64-bit lanes, lane A[x, y] at index x + 5 * y."""
    a = lanes
    for rc in RC:
        c = [a[x] ^ a[x + 5] ^ a[x + 10] ^ a[x + 15] ^ a[x + 20] for x in range(5)]  # theta
        d = [c[(x - 1) % 5] ^ (((c[(x + 1) % 5] << 1) | (c[(x + 1) % 5] >> 63)) & _M) for x in range(5)]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                v = a[x + 5 * y] ^ d[x]
                r = ROT[x][y]  # rho, then pi moves A[x, y] to B[y, 2x + 3y]
                b[y + 5 * ((2 * x + 3 * y) % 5)] = ((v << r) | (v >> (64 - r))) & _M if r else v
        for x in range(5):
            for y in range(5):  # chi
                a[x + 5 * y] = b[x + 5 * y] ^ (~b[(x + 1) % 5 + 5 * y] & b[(x + 2) % 5 + 5 * y])
        a[0] ^= rc  # iota
    return a


def _sponge(data, suffix):
    padded = bytearray(data)
    padded.append(suffix)
    padded.extend(bytes(-len(padded) % RATE))
    padded[-1] |= 0x80
    M = _M
    a00 = a10 = a20 = a30 = a40 = a01 = a11 = a21 = a31 = a41 = 0
    a02 = a12 = a22 = a32 = a42 = a03 = a13 = a23 = a33 = a43 = 0
    a04 = a14 = a24 = a34 = a44 = 0
    for m in struct.iter_unpack("<17Q", padded):
        a00 ^= m[0]
        a10 ^= m[1]
        a20 ^= m[2]
        a30 ^= m[3]
        a40 ^= m[4]
        a01 ^= m[5]
        a11 ^= m[6]
        a21 ^= m[7]
        a31 ^= m[8]
        a41 ^= m[9]
        a02 ^= m[10]
        a12 ^= m[11]
        a22 ^= m[12]
        a32 ^= m[13]
        a42 ^= m[14]
        a03 ^= m[15]
        a13 ^= m[16]
        for rc in RC:
            c0 = a00 ^ a01 ^ a02 ^ a03 ^ a04
            c1 = a10 ^ a11 ^ a12 ^ a13 ^ a14
            c2 = a20 ^ a21 ^ a22 ^ a23 ^ a24
            c3 = a30 ^ a31 ^ a32 ^ a33 ^ a34
            c4 = a40 ^ a41 ^ a42 ^ a43 ^ a44
            d0 = c4 ^ (((c1 << 1) | (c1 >> 63)) & M)
            d1 = c0 ^ (((c2 << 1) | (c2 >> 63)) & M)
            d2 = c1 ^ (((c3 << 1) | (c3 >> 63)) & M)
            d3 = c2 ^ (((c4 << 1) | (c4 >> 63)) & M)
            d4 = c3 ^ (((c0 << 1) | (c0 >> 63)) & M)
            b00 = a00 ^ d0
            t = a01 ^ d0; b13 = ((t << 36) | (t >> 28)) & M
            t = a02 ^ d0; b21 = ((t << 3) | (t >> 61)) & M
            t = a03 ^ d0; b34 = ((t << 41) | (t >> 23)) & M
            t = a04 ^ d0; b42 = ((t << 18) | (t >> 46)) & M
            t = a10 ^ d1; b02 = ((t << 1) | (t >> 63)) & M
            t = a11 ^ d1; b10 = ((t << 44) | (t >> 20)) & M
            t = a12 ^ d1; b23 = ((t << 10) | (t >> 54)) & M
            t = a13 ^ d1; b31 = ((t << 45) | (t >> 19)) & M
            t = a14 ^ d1; b44 = ((t << 2) | (t >> 62)) & M
            t = a20 ^ d2; b04 = ((t << 62) | (t >> 2)) & M
            t = a21 ^ d2; b12 = ((t << 6) | (t >> 58)) & M
            t = a22 ^ d2; b20 = ((t << 43) | (t >> 21)) & M
            t = a23 ^ d2; b33 = ((t << 15) | (t >> 49)) & M
            t = a24 ^ d2; b41 = ((t << 61) | (t >> 3)) & M
            t = a30 ^ d3; b01 = ((t << 28) | (t >> 36)) & M
            t = a31 ^ d3; b14 = ((t << 55) | (t >> 9)) & M
            t = a32 ^ d3; b22 = ((t << 25) | (t >> 39)) & M
            t = a33 ^ d3; b30 = ((t << 21) | (t >> 43)) & M
            t = a34 ^ d3; b43 = ((t << 56) | (t >> 8)) & M
            t = a40 ^ d4; b03 = ((t << 27) | (t >> 37)) & M
            t = a41 ^ d4; b11 = ((t << 20) | (t >> 44)) & M
            t = a42 ^ d4; b24 = ((t << 39) | (t >> 25)) & M
            t = a43 ^ d4; b32 = ((t << 8) | (t >> 56)) & M
            t = a44 ^ d4; b40 = ((t << 14) | (t >> 50)) & M
            a00 = b00 ^ (~b10 & b20)
            a10 = b10 ^ (~b20 & b30)
            a20 = b20 ^ (~b30 & b40)
            a30 = b30 ^ (~b40 & b00)
            a40 = b40 ^ (~b00 & b10)
            a01 = b01 ^ (~b11 & b21)
            a11 = b11 ^ (~b21 & b31)
            a21 = b21 ^ (~b31 & b41)
            a31 = b31 ^ (~b41 & b01)
            a41 = b41 ^ (~b01 & b11)
            a02 = b02 ^ (~b12 & b22)
            a12 = b12 ^ (~b22 & b32)
            a22 = b22 ^ (~b32 & b42)
            a32 = b32 ^ (~b42 & b02)
            a42 = b42 ^ (~b02 & b12)
            a03 = b03 ^ (~b13 & b23)
            a13 = b13 ^ (~b23 & b33)
            a23 = b23 ^ (~b33 & b43)
            a33 = b33 ^ (~b43 & b03)
            a43 = b43 ^ (~b03 & b13)
            a04 = b04 ^ (~b14 & b24)
            a14 = b14 ^ (~b24 & b34)
            a24 = b24 ^ (~b34 & b44)
            a34 = b34 ^ (~b44 & b04)
            a44 = b44 ^ (~b04 & b14)
            a00 ^= rc
    return struct.pack("<4Q", a00, a10, a20, a30)


def keccak256(data):
    """The 32-byte Keccak-256 digest of `data`."""
    return _sponge(data, 0x01)


def sha3_256(data):
    """FIPS 202's SHA3-256 through the same sponge; it exists so the tests can compare with hashlib."""
    return _sponge(data, 0x06)
