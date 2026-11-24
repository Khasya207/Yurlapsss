#pragma once

#include <array>
#include <cstdint>
#include <optional>

namespace yurlaps::decoder {

struct YurlapsFrame {
    std::uint32_t id{};
};

constexpr std::size_t YURLAPS_INIT_SYMBOLS = 4;
constexpr std::size_t YURLAPS_PREAMBLE_SYMBOLS = 16;
constexpr std::size_t YURLAPS_PAYLOAD_BITS = 40;
constexpr std::size_t YURLAPS_PAYLOAD_SYMBOLS = YURLAPS_PAYLOAD_BITS * 2;
constexpr std::size_t YURLAPS_TAIL_SYMBOLS = 4;
constexpr std::size_t YURLAPS_FRAME_SYMBOLS = YURLAPS_INIT_SYMBOLS
    + YURLAPS_PREAMBLE_SYMBOLS + YURLAPS_PAYLOAD_SYMBOLS + YURLAPS_TAIL_SYMBOLS;
constexpr std::uint16_t YURLAPS_PREAMBLE = 0xf9a8;
// CRC16-CCITT polynomial with a YurLaps-specific seed (ASCII "YL"), so frames
// from other protocols or future YLP versions fail the CRC without extra bits.
constexpr std::uint16_t YURLAPS_CRC_SEED = 0x594c;
// Tail symbols, transmitted after the payload (0 = low phase). The decoder
// does not use them; they let the transmitter return to the idle phase.
constexpr std::uint8_t YURLAPS_TAIL_PATTERN = 0x6;  // 0 1 1 0
// IDs are 24-bit and non-zero. The CRC16 protects the ID.
constexpr std::uint32_t YURLAPS_MAX_ID = 0x00ffffffu;

// YLP-1 carries a 24-bit ID and CRC16. K=7 rate-1/2 (133/171) tail-biting coding
// produces 80 soft symbols. The complete frame is 4 init + Barker-13 padded
// to a 16-symbol preamble + 80 payload + 4 tail symbols.
// The input uses a 0..255 confidence scale normalized to the current frame.
std::optional<YurlapsFrame> decode_yurlaps(
    const std::array<std::uint8_t, YURLAPS_PAYLOAD_SYMBOLS>& softbits);

// Encoder used by the firmware prototype and deterministic IQ fixtures.
std::array<std::uint8_t, YURLAPS_PAYLOAD_SYMBOLS> encode_yurlaps(std::uint32_t id);

}  // namespace yurlaps::decoder
