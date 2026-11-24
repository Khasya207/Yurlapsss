#include "yurlaps_decoder/yurlaps.hpp"
#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <utility>

namespace yurlaps::decoder {
namespace {
// K=7, rate 1/2, polynomials 133/171 (octal). 64 states.
constexpr int CONSTRAINT = 7;
constexpr int STATES = 1 << (CONSTRAINT - 1);
constexpr int STATE_MASK = STATES - 1;
constexpr int PAYLOAD_BITS = static_cast<int>(YURLAPS_PAYLOAD_BITS);
constexpr std::uint16_t POLY_A = 0x5b;
constexpr std::uint16_t POLY_B = 0x79;
constexpr int parity(std::uint16_t value) {
    int result = 0;
    while (value != 0u) {
        result ^= static_cast<int>(value & 1u);
        value >>= 1u;
    }
    return result;
}

// OUTPUT[register] = (first << 1) | second, where register holds the new input
// in bit 0 and the previous six inputs above it.
constexpr std::array<std::uint8_t, 1 << CONSTRAINT> make_output_table() {
    std::array<std::uint8_t, 1 << CONSTRAINT> table{};
    for (int reg = 0; reg < (1 << CONSTRAINT); ++reg) {
        table[static_cast<std::size_t>(reg)] = static_cast<std::uint8_t>(
            (parity(static_cast<std::uint16_t>(reg) & POLY_A) << 1)
            | parity(static_cast<std::uint16_t>(reg) & POLY_B));
    }
    return table;
}

constexpr auto OUTPUT = make_output_table();

std::uint16_t crc16(std::uint32_t id) {
    const std::array<std::uint8_t, 3> bytes{
        static_cast<std::uint8_t>(id >> 16u),
        static_cast<std::uint8_t>(id >> 8u),
        static_cast<std::uint8_t>(id),
    };
    std::uint16_t crc = YURLAPS_CRC_SEED;
    for (const auto byte : bytes) {
        crc ^= static_cast<std::uint16_t>(byte) << 8u;
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc & 0x8000u)
                ? static_cast<std::uint16_t>((crc << 1u) ^ 0x1021u)
                : static_cast<std::uint16_t>(crc << 1u);
        }
    }
    return crc;
}

// 40-bit whitening sequence from a 7-bit LFSR (x^7 + x^4 + 1, seed 0x5d).
// XORed onto ID+CRC so that IDs with long runs of zeros or ones do not produce
// long constant stretches in the coded payload.
constexpr std::array<bool, PAYLOAD_BITS> make_whitening() {
    std::array<bool, PAYLOAD_BITS> out{};
    unsigned state = 0x5d;
    for (int index = 0; index < PAYLOAD_BITS; ++index) {
        out[static_cast<std::size_t>(index)] = ((state >> 6) & 1u) != 0u;
        const unsigned feedback = ((state >> 6) ^ (state >> 3)) & 1u;
        state = ((state << 1) | feedback) & 0x7fu;
    }
    return out;
}

constexpr auto WHITENING = make_whitening();

std::array<bool, PAYLOAD_BITS> payload_bits(std::uint32_t id) {
    const auto crc = crc16(id);
    const std::array<std::uint8_t, 5> bytes{
        static_cast<std::uint8_t>(id >> 16u),
        static_cast<std::uint8_t>(id >> 8u),
        static_cast<std::uint8_t>(id),
        static_cast<std::uint8_t>(crc >> 8u),
        static_cast<std::uint8_t>(crc),
    };
    std::array<bool, PAYLOAD_BITS> bits{};
    for (std::size_t index = 0; index < bits.size(); ++index) {
        bits[index] = ((bytes[index / 8] & (0x80u >> (index % 8))) != 0u) != WHITENING[index];
    }
    return bits;
}
}
std::array<std::uint8_t, YURLAPS_PAYLOAD_SYMBOLS> encode_yurlaps(std::uint32_t id) {
    std::array<std::uint8_t, YURLAPS_PAYLOAD_SYMBOLS> encoded{};
    const auto bits = payload_bits(id);
    // Tail-biting: the state after the last bit is the last six input bits,
    // so starting there makes the trellis return to its start state.
    int state = 0;
    for (std::size_t index = bits.size() - (CONSTRAINT - 1); index < bits.size(); ++index) {
        state = ((state << 1) | (bits[index] ? 1 : 0)) & STATE_MASK;
    }
    std::size_t position = 0;
    for (const bool bit : bits) {
        const int reg = ((state << 1) | (bit ? 1 : 0)) & ((1 << CONSTRAINT) - 1);
        const auto pair = OUTPUT[static_cast<std::size_t>(reg)];
        encoded[position++] = (pair & 2u) ? 255u : 0u;
        encoded[position++] = (pair & 1u) ? 255u : 0u;
        state = reg & STATE_MASK;
    }
    return encoded;
}

std::optional<YurlapsFrame> decode_yurlaps(
    const std::array<std::uint8_t, YURLAPS_PAYLOAD_SYMBOLS>& softbits) {
    constexpr int UNREACHABLE = std::numeric_limits<int>::min() / 4;
    int best_score = UNREACHABLE;
    std::array<bool, PAYLOAD_BITS> best_bits{};

    // Branch metric for each step and each output pair (00, 01, 10, 11).
    std::array<std::array<int, 4>, PAYLOAD_BITS> metric{};
    for (int step = 0; step < PAYLOAD_BITS; ++step) {
        const int first = static_cast<int>(softbits[static_cast<std::size_t>(step * 2)]) - 127;
        const int second = static_cast<int>(softbits[static_cast<std::size_t>(step * 2 + 1)]) - 127;
        for (int pair = 0; pair < 4; ++pair) {
            metric[static_cast<std::size_t>(step)][static_cast<std::size_t>(pair)] =
                ((pair & 2) ? first : -first) + ((pair & 1) ? second : -second);
        }
    }

    // Exact maximum-likelihood tail-biting decode: run Viterbi from every start
    // state and keep the best path that returns to the same state.
    // survivors[step][next] bit 0 = predecessor had its top bit set.
    std::array<std::uint64_t, PAYLOAD_BITS> survivors{};
    for (int initial = 0; initial < STATES; ++initial) {
        std::array<int, STATES> score;
        std::array<int, STATES> next_score;
        score.fill(UNREACHABLE);
        score[static_cast<std::size_t>(initial)] = 0;
        for (int step = 0; step < PAYLOAD_BITS; ++step) {
            std::uint64_t chosen = 0;
            const auto& m = metric[static_cast<std::size_t>(step)];
            for (int next = 0; next < STATES; ++next) {
                const int input = next & 1;
                const int low = next >> 1;
                const int high = low | (STATES >> 1);
                const int reg_low = (low << 1) | input;
                const int reg_high = (high << 1) | input;
                const int from_low = score[static_cast<std::size_t>(low)] == UNREACHABLE ? UNREACHABLE
                    : score[static_cast<std::size_t>(low)] + m[OUTPUT[static_cast<std::size_t>(reg_low)]];
                const int from_high = score[static_cast<std::size_t>(high)] == UNREACHABLE ? UNREACHABLE
                    : score[static_cast<std::size_t>(high)] + m[OUTPUT[static_cast<std::size_t>(reg_high)]];
                if (from_high > from_low) {
                    next_score[static_cast<std::size_t>(next)] = from_high;
                    chosen |= std::uint64_t{1} << next;
                } else {
                    next_score[static_cast<std::size_t>(next)] = from_low;
                }
            }
            survivors[static_cast<std::size_t>(step)] = chosen;
            score = next_score;
        }
        const int final_score = score[static_cast<std::size_t>(initial)];
        if (final_score == UNREACHABLE || final_score <= best_score) continue;
        best_score = final_score;
        int state = initial;
        for (int step = PAYLOAD_BITS; step > 0; --step) {
            best_bits[static_cast<std::size_t>(step - 1)] = (state & 1) != 0;
            const int top = static_cast<int>((survivors[static_cast<std::size_t>(step - 1)] >> state) & 1u);
            state = (state >> 1) | (top ? (STATES >> 1) : 0);
        }
    }

    if (best_score == UNREACHABLE) return std::nullopt;
    std::array<std::uint8_t, 5> bytes{};
    for (int bit = 0; bit < PAYLOAD_BITS; ++bit) {
        bytes[static_cast<std::size_t>(bit / 8)] = static_cast<std::uint8_t>(
            (bytes[static_cast<std::size_t>(bit / 8)] << 1u)
            | ((best_bits[static_cast<std::size_t>(bit)] != WHITENING[static_cast<std::size_t>(bit)]) ? 1u : 0u));
    }
    const std::uint32_t id = (static_cast<std::uint32_t>(bytes[0]) << 16u)
        | (static_cast<std::uint32_t>(bytes[1]) << 8u) | bytes[2];
    const std::uint16_t received_crc = static_cast<std::uint16_t>(
        (static_cast<std::uint16_t>(bytes[3]) << 8u) | bytes[4]);
    if (id == 0u || id > YURLAPS_MAX_ID || received_crc != crc16(id)) {
        return std::nullopt;
    }
    return YurlapsFrame{id};
}
} // namespace yurlaps::decoder
