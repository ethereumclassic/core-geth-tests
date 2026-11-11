import hashlib
import random
import struct
import unittest

import keccak

# Published Keccak-256 digests. The empty input: ethereumclassic/core-geth, core/types/hashes.go,
# `EmptyCodeHash = crypto.Keccak256Hash(nil)`. "abc": golang.org/x/crypto, sha3/sha3_test.go,
# TestKeccak, and ethereumclassic/core-geth, crypto/crypto_test.go, TestKeccak256Hash.
PUBLISHED = {
    b"": "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
    b"abc": "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
}


def reference_sponge(data, suffix):
    """The sponge built on `keccak.permute`, the straightforward transcription of FIPS 202."""
    padded = bytearray(data) + bytes([suffix])
    padded += bytes(-len(padded) % keccak.RATE)
    padded[-1] |= 0x80
    lanes = [0] * 25
    for block in struct.iter_unpack("<17Q", padded):
        for i, word in enumerate(block):
            lanes[i] ^= word
        keccak.permute(lanes)
    return struct.pack("<4Q", *lanes[:4])


class KeccakTest(unittest.TestCase):
    def test_published_vectors(self):
        for message, digest in PUBLISHED.items():
            self.assertEqual(keccak.keccak256(message).hex(), digest, message)

    def test_hashlib_sha3_is_not_keccak(self):
        """The negative control: FIPS 202's padding gives a different digest for the same input."""
        for message, digest in PUBLISHED.items():
            self.assertNotEqual(hashlib.sha3_256(message).hexdigest(), digest)
            self.assertNotEqual(hashlib.sha3_256(message).digest(), keccak.keccak256(message))

    def test_permutation_and_sponge_against_hashlib(self):
        """With SHA-3's padding the same sponge must equal hashlib's, at every length up to three
        blocks and beyond, so the permutation and the absorbing are right whatever the padding."""
        rng = random.Random(14)
        lengths = list(range(0, 3 * keccak.RATE + 2)) + [1000, 4096, 65537]
        for n in lengths:
            message = bytes(rng.getrandbits(8) for _ in range(n))
            self.assertEqual(keccak.sha3_256(message), hashlib.sha3_256(message).digest(), n)

    def test_unrolled_round_matches_the_transcription(self):
        rng = random.Random(1600)
        for n in list(range(0, 2 * keccak.RATE + 2)) + [5000]:
            message = bytes(rng.getrandbits(8) for _ in range(n))
            self.assertEqual(keccak.keccak256(message), reference_sponge(message, 0x01), n)

    def test_padding_edges(self):
        """A message one byte short of a block puts both padding bits in one byte (0x81); a full
        block needs a second block of padding. Both sides of each edge must differ and agree with
        the transcription."""
        for n in (keccak.RATE - 1, keccak.RATE, keccak.RATE + 1):
            message = b"\xa3" * n
            self.assertEqual(keccak.keccak256(message), reference_sponge(message, 0x01))
        digests = {keccak.keccak256(b"\x00" * n) for n in (keccak.RATE - 1, keccak.RATE, keccak.RATE + 1)}
        self.assertEqual(len(digests), 3)


if __name__ == "__main__":
    unittest.main()
