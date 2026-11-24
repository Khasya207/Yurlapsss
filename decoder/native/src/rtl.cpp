#include "yurlaps_decoder/rtl.hpp"

#include <chrono>
#include <cmath>
#include <fstream>
#include <iostream>
#include <string_view>
#include <sstream>

#ifdef YURLAPS_WITH_RTLSDR
#include <rtl-sdr.h>

namespace yurlaps::decoder {
namespace {

struct Context {
    IqDecoder* decoder{};
    std::uint64_t start_us{};
    double sample_rate{};
    std::ofstream* record{};
};

std::uint64_t steady_us() {
    return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
}

void receive(unsigned char* bytes, std::uint32_t length, void* opaque) {
    auto* context = static_cast<Context*>(opaque);
    if (context->record && *context->record) {
        context->record->write(reinterpret_cast<const char*>(bytes),
                               static_cast<std::streamsize>(length));
    }
    // librtlsdr invokes the callback after the buffer has been filled. Pass
    // the timestamp for its first sample, not the callback's end time.
    const auto callback_us = steady_us() - context->start_us;
    const auto sample_count = static_cast<std::uint64_t>(length / 2u);
    const auto buffer_us = static_cast<std::uint64_t>(std::llround(
        static_cast<double>(sample_count) * 1'000'000.0 / context->sample_rate));
    context->decoder->feed(bytes, length,
        callback_us > buffer_us ? callback_us - buffer_us : 0);
}

bool is_blog_v4(rtlsdr_dev_t* device, std::uint32_t index) {
    if (rtlsdr_get_tuner_type(device) == RTLSDR_TUNER_R828D) return true;
    char manufacturer[256]{};
    char product[256]{};
    char serial[256]{};
    if (rtlsdr_get_device_usb_strings(index, manufacturer, product, serial) != 0) return false;
    const std::string_view name(product);
    return name.find("V4") != std::string_view::npos
        || name.find("Blog") != std::string_view::npos;
}

}  // namespace

int list_rtl_devices(std::ostream& out) {
    const std::uint32_t count = rtlsdr_get_device_count();
    for (std::uint32_t index = 0; index < count && index < 32; ++index) {
        char manufacturer[256]{};
        char product[256]{};
        char serial[256]{};
        if (rtlsdr_get_device_usb_strings(index, manufacturer, product, serial) != 0) continue;
        out << "YURLAPS_DEVICE\t" << index << '\t' << serial << '\n';
    }
    return 0;
}

int run_rtl_sdr(const RtlOptions& options, IqDecoder& decoder, std::string& error) {
    rtlsdr_dev_t* device = nullptr;
    std::uint32_t index = 0;
    if (!options.serial.empty()) {
        const int found = rtlsdr_get_index_by_serial(options.serial.c_str());
        if (found < 0) {
            error = "no device with serial " + options.serial + " (device not found)";
            return 2;
        }
        index = static_cast<std::uint32_t>(found);
    }
    int result = rtlsdr_open(&device, index);
    if (result != 0) {
        error = "failed to open RTL-SDR index " + std::to_string(index)
              + " (rtlsdr_open code " + std::to_string(result) + "; no supported devices?)";
        return 2;
    }
    auto close = [&] { rtlsdr_close(device); };

    const bool blog_v4 = is_blog_v4(device, index);
    if (!blog_v4 || options.force_direct_sampling) {
        // Standard RTL2832U dongles need direct sampling to receive the 5 MHz
        // carrier. Blog V4 has its own upconverter, so bypassing the tuner
        // on it would produce no passings.
        if ((result = rtlsdr_set_direct_sampling(device, 2)) != 0) {
            error = "rtlsdr_set_direct_sampling failed: " + std::to_string(result);
            close();
            return 2;
        }
    }
    if ((result = rtlsdr_set_sample_rate(device, options.sample_rate)) != 0) {
        error = "rtlsdr_set_sample_rate failed: " + std::to_string(result); close(); return 2;
    }
    if ((result = rtlsdr_set_center_freq(device, options.center_frequency_hz)) != 0) {
        error = "rtlsdr_set_center_freq failed: " + std::to_string(result); close(); return 2;
    }
    if ((result = rtlsdr_set_tuner_bandwidth(device, 1'700'000)) != 0) {
        error = "rtlsdr_set_tuner_bandwidth failed: " + std::to_string(result); close(); return 2;
    }
    if ((result = rtlsdr_set_tuner_gain_mode(device, 1)) != 0) {
        error = "rtlsdr_set_tuner_gain_mode failed: " + std::to_string(result); close(); return 2;
    }
    if ((result = rtlsdr_set_tuner_gain(device, static_cast<int>(std::lround(options.gain_db * 10.0)))) != 0) {
        error = "rtlsdr_set_tuner_gain failed: " + std::to_string(result); close(); return 2;
    }
    if (options.bias_tee) {
        if ((result = rtlsdr_set_bias_tee(device, 1)) != 0) {
            error = "rtlsdr_set_bias_tee failed: " + std::to_string(result); close(); return 2;
        }
    }
    std::ofstream record;
    if (!options.record_path.empty()) {
        record.open(options.record_path, std::ios::binary | std::ios::trunc);
        if (!record) {
            error = "cannot open IQ output file: " + options.record_path;
            close();
            return 2;
        }
    }
    rtlsdr_reset_buffer(device);
    // The host app reads this line to show frequency and gain.
    std::cerr << "YurLaps Decoder RX: freq=" << options.center_frequency_hz
              << " rate=" << options.sample_rate << " gain=" << options.gain_db
              << (blog_v4 ? " tuner=V4" : " tuner=direct") << std::endl;
    Context context{&decoder, steady_us(), static_cast<double>(options.sample_rate),
                    record.is_open() ? &record : nullptr};
    result = rtlsdr_read_async(device, receive, &context, 0, 16'384);
    close();
    record.close();
    if (result != 0) { error = "rtlsdr_read_async failed: " + std::to_string(result); return 2; }
    return 0;
}

}  // namespace yurlaps::decoder

#else

namespace yurlaps::decoder {
int list_rtl_devices(std::ostream&) { return 0; }
int run_rtl_sdr(const RtlOptions&, IqDecoder&, std::string& error) {
    error = "RTL-SDR backend not compiled in; build with -DYURLAPS_WITH_RTLSDR=ON";
    return 2;
}
}  // namespace yurlaps::decoder

#endif
