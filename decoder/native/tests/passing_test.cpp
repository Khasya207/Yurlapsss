#include "yurlaps_decoder/passing.hpp"

#include <cassert>
#include <vector>

int main() {
    using yurlaps::decoder::HitSample;
    const std::vector<HitSample> short_pass{
        {100, -12.0f, 21.0f},
        {140, -10.0f, 26.0f},
        {180, -13.0f, 19.0f},
    };
    const auto short_estimate = yurlaps::decoder::estimate_passing(short_pass, 2'500'000.0);
    assert(short_estimate.sample_index > 100 && short_estimate.sample_index < 180);
    assert(short_estimate.duration_us > 0);
    assert(short_estimate.mer_db == 26.0f);

    std::vector<HitSample> waveform;
    for (std::uint64_t i = 0; i < 20; ++i) {
        const float value = (i == 4 || i == 15) ? -5.0f : -25.0f;
        waveform.push_back({i * 10'000, value, 20.0f});
    }
    const auto waveform_estimate = yurlaps::decoder::estimate_passing(waveform, 2'500'000.0);
    assert(waveform_estimate.sample_index > waveform.front().sample_index);
    assert(waveform_estimate.sample_index < waveform.back().sample_index);
}
