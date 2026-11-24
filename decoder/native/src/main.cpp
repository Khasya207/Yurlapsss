#include "yurlaps_decoder/protocol.hpp"
#include "yurlaps_decoder/iq.hpp"
#include "yurlaps_decoder/rtl.hpp"
#include "yurlaps_decoder/zmq.hpp"

#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

constexpr const char* USAGE =
    "Usage:\n"
    "  YurLapsDecoder --iq capture.iq [--debug]\n"
    "  YurLapsDecoder [--rtl [serial] [gain_db]] [-g gain_db] [-d serial] [-p port]\n"
    "                 [-b] [-D] [--debug] [--quiet] [--record file.iq]\n"
    "  YurLapsDecoder --list-devices\n"
    "  YurLapsDecoder --protocol-check\n"
    "With no arguments, lines of text are read from stdin and echoed.\n"
    "-b bias tee on, -D force direct sampling, -t/-m accepted and ignored.\n"
    "With ZeroMQ, every line is also published on tcp://127.0.0.1:<port> (default 5556).\n";

// The noise floor is -inf until it has an estimate. Downstream JSON cannot
// carry infinity, so report a finite floor instead.
double finite_noise(float value) {
    return std::isfinite(value) ? static_cast<double>(value) : -99.0;
}

std::string passing_line(const yurlaps::decoder::IqPassing& passing) {
    std::ostringstream line;
    line << "P " << passing.timestamp_us / 1000 << " YUL " << passing.id << ' '
         << std::fixed << std::setprecision(2) << passing.rssi_db << ' ' << passing.hits
         << ' ' << passing.duration_us << ' ' << std::setprecision(1) << passing.mer_db;
    return line.str();
}

std::string status_line(const yurlaps::decoder::IqStatus& status) {
    std::ostringstream line;
    line << "S " << status.timestamp_us / 1000 << ' ' << std::fixed << std::setprecision(2)
         << finite_noise(status.noise_floor_db) << ' ' << status.dc_offset << ' '
         << status.frames_received << ' ' << status.frames_processed << ' '
         << status.jitter_p95_ms;
    return line.str();
}

std::string hit_line(const yurlaps::decoder::IqHit& hit) {
    std::ostringstream line;
    line << "H " << hit.timestamp_us / 1000 << " YUL " << hit.id << ' ' << std::fixed
         << std::setprecision(2) << hit.rssi_db << ' ' << std::setprecision(1) << hit.mer_db
         << ' ' << static_cast<unsigned>(hit.status);
    return line.str();
}

struct Options {
    std::string iq_path;
    std::string serial;
    std::string record_path;
    double gain_db = 28.0;
    int port = 0;  // 0 = default endpoint, not required to bind
    bool debug = false;
    bool quiet = false;
    bool bias_tee = false;
    bool direct_sampling = false;
    bool list_devices = false;
    bool protocol_check = false;
    bool rtl_mode = false;
};

bool parse_number(const std::string& text, double& value) {
    char* end = nullptr;
    value = std::strtod(text.c_str(), &end);
    return end != text.c_str() && *end == '\0' && std::isfinite(value);
}

// Returns an error message, or an empty string when the arguments are fine.
std::string parse_args(int argc, char** argv, Options& options) {
    int positional = 0;
    for (int index = 1; index < argc; ++index) {
        const std::string arg = argv[index];
        auto value_of = [&](const char* name) -> const char* {
            if (index + 1 >= argc) return nullptr;
            (void)name;
            return argv[++index];
        };
        if (arg == "--iq") {
            const char* value = value_of("--iq");
            if (!value) return "--iq needs a file name";
            options.iq_path = value;
        } else if (arg == "--debug") {
            options.debug = true;
        } else if (arg == "--quiet") {
            options.quiet = true;
        } else if (arg == "--record") {
            const char* value = value_of("--record");
            if (!value) return "--record needs a file name";
            options.record_path = value;
        } else if (arg == "--list-devices") {
            options.list_devices = true;
        } else if (arg == "--protocol-check") {
            options.protocol_check = true;
        } else if (arg == "--rtl") {
            options.rtl_mode = true;
            positional = 0;
        } else if (arg == "-g") {
            const char* value = value_of("-g");
            if (!value || !parse_number(value, options.gain_db) || options.gain_db < 0.0
                || options.gain_db > 60.0) return "-g needs a gain in dB (0..60)";
            options.rtl_mode = true;
        } else if (arg == "-d") {
            const char* value = value_of("-d");
            if (!value) return "-d needs a serial";
            options.serial = value;
            options.rtl_mode = true;
        } else if (arg == "-p") {
            const char* value = value_of("-p");
            double port = 0.0;
            if (!value || !parse_number(value, port) || port < 1024.0 || port > 65535.0)
                return "-p needs a port (1024..65535)";
            options.port = static_cast<int>(port);
            options.rtl_mode = true;
        } else if (arg == "-b") {
            options.bias_tee = true;
            options.rtl_mode = true;
        } else if (arg == "-D") {
            options.direct_sampling = true;
            options.rtl_mode = true;
        } else if (arg == "-t" || arg == "-m") {
            options.rtl_mode = true;  // accepted for compatibility with the host app
        } else if (options.rtl_mode && !arg.empty() && arg[0] != '-' && positional == 0) {
            options.serial = arg;
            ++positional;
        } else if (options.rtl_mode && !arg.empty() && arg[0] != '-' && positional == 1) {
            if (!parse_number(arg, options.gain_db)) return "gain must be a number";
            ++positional;
        } else {
            return "unknown option: " + arg;
        }
    }
    return {};
}

int run_iq(const Options& options) {
    std::ifstream input(options.iq_path, std::ios::binary);
    if (!input) {
        std::cerr << "cannot open IQ capture: " << options.iq_path << '\n';
        return 2;
    }
    yurlaps::decoder::IqDecoder decoder;
    decoder.set_callback([](const yurlaps::decoder::IqPassing& passing) {
        std::cout << passing_line(passing) << '\n' << std::flush;
    });
    decoder.set_status_callback([](const yurlaps::decoder::IqStatus& status) {
        std::cout << status_line(status) << '\n' << std::flush;
    });
    if (options.debug) {
        decoder.set_hit_callback([](const yurlaps::decoder::IqHit& hit) {
            std::cout << hit_line(hit) << '\n' << std::flush;
        });
    }
    std::vector<std::uint8_t> chunk(64 * 1024);
    while (input) {
        input.read(reinterpret_cast<char*>(chunk.data()), static_cast<std::streamsize>(chunk.size()));
        const auto count = input.gcount();
        if (count > 0) decoder.feed(chunk.data(), static_cast<std::size_t>(count));
    }
    decoder.flush();
    return 0;
}

int run_rtl(const Options& options) {
    yurlaps::decoder::RtlOptions rtl;
    rtl.serial = options.serial;
    rtl.gain_db = options.gain_db;
    rtl.bias_tee = options.bias_tee;
    rtl.force_direct_sampling = options.direct_sampling;
    rtl.record_path = options.record_path;

    yurlaps::decoder::IqDecoder decoder;
    yurlaps::decoder::Publisher publisher;
    std::string publisher_error;
    const std::string endpoint = "tcp://127.0.0.1:" + std::to_string(options.port ? options.port : 5556);
    const bool bound = publisher.bind(endpoint, publisher_error);
    if (!bound) {
        std::cerr << "ZeroMQ bind failed on " << endpoint << ": " << publisher_error << '\n';
        // A host app that asked for a port cannot work without it.
        if (options.port) return 3;
    }
    const bool quiet = options.quiet;
    decoder.set_callback([&publisher, quiet](const yurlaps::decoder::IqPassing& passing) {
        const auto line = passing_line(passing);
        if (!quiet) std::cout << line << '\n' << std::flush;
        publisher.send(line);
    });
    decoder.set_status_callback([&publisher, quiet](const yurlaps::decoder::IqStatus& status) {
        const auto line = status_line(status);
        if (!quiet) std::cout << line << '\n' << std::flush;
        publisher.send(line);
    });
    if (options.debug) {
        decoder.set_hit_callback([quiet](const yurlaps::decoder::IqHit& hit) {
            if (!quiet) std::cout << hit_line(hit) << '\n' << std::flush;
        });
    }
    std::string error;
    const int result = yurlaps::decoder::run_rtl_sdr(rtl, decoder, error);
    decoder.flush();
    if (result != 0) std::cerr << error << '\n';
    return result;
}

}  // namespace

int main(int argc, char** argv) {
    Options options;
    if (argc > 1) {
        const auto error = parse_args(argc, argv, options);
        if (!error.empty()) {
            std::cerr << error << '\n' << USAGE;
            return 2;
        }
    }
    if (options.protocol_check) {
        // The host app looks for this exact token before it launches the decoder.
        std::cout << "YURLAPS_YLP1=1\n";
        return 0;
    }
    if (options.list_devices) return yurlaps::decoder::list_rtl_devices(std::cout);
    if (!options.iq_path.empty()) return run_iq(options);
    if (options.rtl_mode) return run_rtl(options);
    if (argc > 1) {
        std::cerr << USAGE;
        return 2;
    }
    for (std::string line; std::getline(std::cin, line);) {
        if (const auto event = yurlaps::decoder::parse_line(line)) {
            std::cout << yurlaps::decoder::format_event(*event) << '\n' << std::flush;
        }
    }
    return 0;
}
