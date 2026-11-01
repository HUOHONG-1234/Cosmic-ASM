"""Generate examples/aes128.cos: AES-128 single-block ECB encryption."""
from pathlib import Path

# FIPS-197 S-box
SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
]

KEY_HEX = "000102030405060708090a0b0c0d0e0f"
PT_HEX = "00112233445566778899aabbccddeeff"


def gmul(a: int, b: int) -> int:
    """Multiply in GF(2^8) with the AES polynomial x^8+x^4+x^3+x+1."""
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def expand_key(key: bytes) -> list[bytes]:
    """Return 11 round keys of 16 bytes each."""
    words = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        temp = list(words[i - 1])
        if i % 4 == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[b] for b in temp]
            temp[0] ^= rcon
            rcon = gmul(rcon, 2)
        words.append([words[i - 4][j] ^ temp[j] for j in range(4)])
    return [bytes(b for w in words[r * 4:r * 4 + 4] for b in w) for r in range(11)]


def main() -> None:
    key = bytes.fromhex(KEY_HEX)
    round_keys = expand_key(key)
    hex_sbox = ", ".join(f"0x{v:02x}" for v in SBOX)
    hex_keys = ", ".join(f"0x{b:02x}" for rk in round_keys for b in rk)
    pt_list = ", ".join(str(b) for b in bytes.fromhex(PT_HEX))
    m9_state: dict[int, int] = {}

    L: list[str] = []
    add = L.append
    add("#% v5 AES-128 encryption of one 16-byte block (FIPS-197).")
    add("#% Exercises v5 bitwise operators, -uint32 wraparound and bytes arrays.")
    add("#% This is a correctness demo, not a constant-time crypto implementation.")
    add("#%math = uint32")
    add("call = (math)")
    add("#% AES S-box")
    add(f"bytes SB = [{hex_sbox}]")
    add("#% Expanded round keys: 11 rounds x 16 bytes (176 bytes)")
    add(f"bytes RK = [{hex_keys}]")
    add("#% Plaintext block")
    add(f"bytes PT = [{pt_list}]")
    add("#% GF(2^8) multiply by 2 with the AES reduction polynomial")
    add("function xt(v) = ((v << 1) & 255) ^ ((v >> 7) * 27)")
    add("")

    def load(src_bytes: str, names: list[str], prefix_math: str) -> None:
        for i, name in enumerate(names):
            add(f"math_run bytes_get({src_bytes}, {i}) = ?")
            add(f"if up = (ok) goto (push ({{{name}}} = {{(in = up)}}))")

    s = [f"s{i}" for i in range(16)]
    load("PT", s, "")
    add("")

    add("#% AddRoundKey with round key 0")
    for i in range(16):
        add(f"math_run {{s{i}}} ^ bytes_get(RK, {i}) = ?")
        add(f"if up = (ok) goto (push ({{s{i}}} = {{(in = up)}}))")
    add("")

    for rnd in range(1, 11):
        final = rnd == 10
        add(f"#% Round {rnd}" + (" - final round, no MixColumns" if final else ""))
        add("#% SubBytes + ShiftRows (state is column-major: s[column*4 + row])")
        for c in range(4):
            for r in range(4):
                src_index = ((c + r) % 4) * 4 + r
                add(f"math_run bytes_get(SB, {{s{src_index}}}) = ?")
                add(f"if up = (ok) goto (push ({{t{c * 4 + r}}} = {{(in = up)}}))")
        for i in range(16):
            add(f"push ({{s{i}}} = {{t{i}}})")
        add("")

        if not final:
            add("#% MixColumns")
            for c in range(4):
                base = c * 4
                a0, a1, a2, a3 = (f"{{s{base + k}}}" for k in range(4))
                cols = (
                    f"xt({a0}) ^ (xt({a1}) ^ {a1}) ^ {a2} ^ {a3}",
                    f"{a0} ^ xt({a1}) ^ (xt({a2}) ^ {a2}) ^ {a3}",
                    f"{a0} ^ {a1} ^ xt({a2}) ^ (xt({a3}) ^ {a3})",
                    f"(xt({a0}) ^ {a0}) ^ {a1} ^ {a2} ^ xt({a3})",
                )
                for k, expr in enumerate(cols):
                    add(f"math_run {expr} = ?")
                    add(f"if up = (ok) goto (push ({{m{c}_{k}}} = {{(in = up)}}))")
                for k in range(4):
                    add(f"push ({{s{base + k}}} = {{m{c}_{k}}})")
            add("")

        add("#% AddRoundKey")
        for i in range(16):
            add(f"math_run {{s{i}}} ^ bytes_get(RK, {rnd * 16 + i}) = ?")
            add(f"if up = (ok) goto (push ({{s{i}}} = {{(in = up)}}))")
        add("")

    add("#% Assemble the ciphertext as hex")
    for i in range(16):
        add(f"math_run hex({{s{i}}}, 2) = ?")
        add(f"if up = (ok) goto (push ({{h{i}}} = {{(in = up)}}))")
    add("push ({cipher} = {h0})")
    for i in range(1, 16):
        add(f"push ({{cipher}} = {{cipher}}{{h{i}}})")
    add("print (<{cipher}>)")

    target = Path(__file__).resolve().parent.parent / "examples" / "aes128.cos"
    target.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {target} ({len(L)} lines)")


if __name__ == "__main__":
    main()
