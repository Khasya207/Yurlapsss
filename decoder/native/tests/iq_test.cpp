#include "yurlaps_decoder/iq.hpp"
#include "yurlaps_decoder/yurlaps.hpp"

#include <cassert>
#include <complex>
#include <cstdint>
#include <iostream>
#include <vector>

using namespace yurlaps::decoder;

int main() {
    std::vector<IqSample> samples;
    const auto payload = encode_yurlaps(2504260u);
    const auto append_symbol = [&samples](float sign) {
        const auto value = std::polar(0.8f, 0.71f) * sign;
        samples.push_back({value.real(), value.imag()});
        samples.push_back({value.real(), value.imag()});
    };
    for (int repeat = 0; repeat < 3; ++repeat) {
        for (int symbol = 0; symbol < 4; ++symbol) append_symbol(-1.0f);
        for (int symbol = 0; symbol < 16; ++symbol) {
            append_symbol((YURLAPS_PREAMBLE & (1u << (15 - symbol))) ? 1.0f : -1.0f);
        }
        for (const auto bit : payload) append_symbol(bit > 127u ? 1.0f : -1.0f);
        for (int symbol = 0; symbol < 4; ++symbol) {
            append_symbol((YURLAPS_TAIL_PATTERN & (1u << (3 - symbol))) ? 1.0f : -1.0f);
        }
        samples.insert(samples.end(), 100, {0.0f, 0.0f});
    }

    IqDecoder decoder;
    std::vector<IqPassing> passings;
    decoder.set_callback([&passings](const IqPassing& passing) { passings.push_back(passing); });
    decoder.feed(samples);
    decoder.flush();
    assert(passings.size() == 1);
    assert(passings.front().protocol == IqProtocol::YURLAPS);
    assert(passings.front().id == 2504260u);
    assert(passings.front().hits == 3u);
    assert(passings.front().mer_db > 20.0f);

    IqDecoder timed_decoder;
    std::vector<IqPassing> timed;
    timed_decoder.set_callback([&timed](const IqPassing& passing) { timed.push_back(passing); });
    std::vector<IqSample> prefix(500, {0.0f, 0.0f});
    timed_decoder.feed(prefix, 100'000);
    timed_decoder.feed(samples, 300'000);
    timed_decoder.flush();
    assert(timed.size() == 1);
    assert(timed.front().timestamp_us >= 300'000);

    std::cout << "iq tests passed\n";
}
