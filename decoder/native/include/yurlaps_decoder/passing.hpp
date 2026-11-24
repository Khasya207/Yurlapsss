#pragma once

#include <cstdint>
#include <vector>

namespace yurlaps::decoder {

struct HitSample {
    std::uint64_t sample_index{};
    float rssi_db{};
    float mer_db{};
};

struct PassingEstimate {
    std::uint64_t sample_index{};
    std::uint64_t duration_us{};
    float rssi_db{};
    float mer_db{};
};

// Estimate the crossing point from the complete YLP waveform. Short windows
// use an amplitude-weighted center. Longer windows use a smoothed envelope
// crossing and report a duration only when both crossings are visible.
PassingEstimate estimate_passing(const std::vector<HitSample>& hits, double sample_rate);

}  // namespace yurlaps::decoder
