#pragma once

#include <iosfwd>
#include <string>

#include "yurlaps_decoder/iq.hpp"

namespace yurlaps::decoder {

struct RtlOptions {
    std::string serial;
    // When non-empty, the raw unsigned 8-bit interleaved IQ stream is also
    // written to this file (replayable with `--iq <file> --debug`).
    std::string record_path;
    double gain_db = 28.0;
    bool bias_tee = false;
    // Force direct sampling (mode 2) even on a Blog V4. Off by default.
    bool force_direct_sampling = false;
    std::uint64_t center_frequency_hz = 5'000'000;
    std::uint32_t sample_rate = 2'500'000;
};

// Prints one `YURLAPS_DEVICE<TAB>index<TAB>serial` line per attached dongle.
int list_rtl_devices(std::ostream& out);

// Blocks until the RTL-SDR stream ends or the device reports an error. The
// build enables the hardware backend only when librtlsdr is found.
int run_rtl_sdr(const RtlOptions& options, IqDecoder& decoder, std::string& error);

}  // namespace yurlaps::decoder
