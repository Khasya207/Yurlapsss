#include "yurlaps_decoder/passing.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <numeric>

namespace yurlaps::decoder {
namespace {

constexpr float EDGE_DB = 6.0f;
constexpr std::uint64_t MIN_DURATION_SAMPLES = 4;

std::vector<HitSample> ordered(const std::vector<HitSample>& input) {
    auto hits = input;
    std::stable_sort(hits.begin(), hits.end(), [](const HitSample& left, const HitSample& right) {
        return left.sample_index < right.sample_index;
    });
    return hits;
}

std::uint64_t crossing(const HitSample& left, const HitSample& right, float level) {
    if (right.sample_index <= left.sample_index) return left.sample_index;
    const float rise = right.rssi_db - left.rssi_db;
    if (std::fabs(rise) < 0.001f) return left.sample_index;
    const float fraction = std::clamp((level - left.rssi_db) / rise, 0.0f, 1.0f);
    return left.sample_index + static_cast<std::uint64_t>(std::llround(
        static_cast<double>(right.sample_index - left.sample_index) * fraction));
}

}  // namespace

PassingEstimate estimate_passing(const std::vector<HitSample>& input, double sample_rate) {
    const auto hits = ordered(input);
    if (hits.empty()) return {};

    const auto peak = std::max_element(hits.begin(), hits.end(), [](const HitSample& left,
                                                                    const HitSample& right) {
        return left.rssi_db < right.rssi_db;
    });
    const float threshold = peak->rssi_db - EDGE_DB;
    const double amplitude_sum = std::accumulate(hits.begin(), hits.end(), 0.0,
        [threshold](double total, const HitSample& hit) {
            return total + (hit.rssi_db >= threshold
                ? std::pow(10.0, static_cast<double>(hit.rssi_db) / 20.0) : 0.0);
        });
    const double weighted_time = std::accumulate(hits.begin(), hits.end(), 0.0,
        [threshold](double total, const HitSample& hit) {
            if (hit.rssi_db < threshold) return total;
            return total + static_cast<double>(hit.sample_index)
                * std::pow(10.0, static_cast<double>(hit.rssi_db) / 20.0);
        });
    std::uint64_t passing_sample = peak->sample_index;
    if (amplitude_sum > 0.0) {
        passing_sample = static_cast<std::uint64_t>(std::llround(weighted_time / amplitude_sum));
    }

    std::uint64_t duration_samples = 0;
    if (hits.size() >= 3 && hits.back().sample_index > hits.front().sample_index) {
        std::size_t first = 0;
        while (first + 1 < hits.size() && hits[first + 1].rssi_db < threshold) ++first;
        std::size_t last = hits.size() - 1;
        while (last > first && hits[last - 1].rssi_db < threshold) --last;
        if (last > first) {
            const auto start = first > 0 ? crossing(hits[first - 1], hits[first], threshold)
                                         : hits[first].sample_index;
            const auto end = last + 1 < hits.size()
                ? crossing(hits[last], hits[last + 1], threshold) : hits[last].sample_index;
            if (end > start && end - start >= MIN_DURATION_SAMPLES) {
                duration_samples = end - start;
                passing_sample = start + duration_samples / 2;
            }
        }
    }

    const auto duration_us = static_cast<std::uint64_t>(std::llround(
        static_cast<double>(duration_samples) * 1'000'000.0 / sample_rate));
    return {passing_sample, duration_us, peak->rssi_db, peak->mer_db};
}

}  // namespace yurlaps::decoder
