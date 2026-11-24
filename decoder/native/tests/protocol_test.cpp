#include "yurlaps_decoder/protocol.hpp"
#include "yurlaps_decoder/yurlaps.hpp"

#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>

using namespace yurlaps::decoder;

int main() {
    const auto encoded = encode_yurlaps(2504260u);
    const auto decoded = decode_yurlaps(encoded);
    assert(decoded && decoded->id == 2504260u);
    // Golden vectors from tools/gen_firmware.py: the Python and C++ encoders must agree.
    struct Golden { std::uint32_t id; const char* symbols; };
    const Golden golden[] = {
        {2504260u, "00101000111110001000111011000011111000111011000011001101101001001110100101111110"},
        {1u, "01100001111100111000100110000111101000011011110001010110000001010101011001000100"},
        {16777215u, "00010011110011000111011001111000010111100100000000101011111000000101001110101011"},
    };
    for (const auto& g : golden) {
        const auto e = encode_yurlaps(g.id);
        for (std::size_t k = 0; k < e.size(); ++k) assert((e[k] > 127u ? '1' : '0') == g.symbols[k]);
    }
    auto damaged = encoded;
    damaged[10] = static_cast<std::uint8_t>(255u - damaged[10]);
    assert(decode_yurlaps(damaged));
    for (const std::uint32_t id : {1u, 12000000u, 0x00ffffffu}) {
        const auto high = decode_yurlaps(encode_yurlaps(id));
        assert(high && high->id == id);
    }

    const auto passing = parse_line("P 100 YUL 2504260 -12.5 3 80 22.4");
    assert(passing && std::holds_alternative<Passing>(*passing));
    assert(std::get<Passing>(*passing).type == TransponderType::YURLAPS);
    assert(std::get<Passing>(*passing).id == 2504260u);
    assert(!parse_line("P 100 LEGACY 2504260 -12.5 3 80 22.4"));
    assert(!parse_line("T 100 LEGACY 2504260 10"));
    assert(!parse_line("L 100 START -8.3"));

    const auto status = parse_line("S 100 -41.0 2.0 4 3 0.2");
    assert(status && std::holds_alternative<Status>(*status));
    std::cout << "protocol tests passed\n";
}
