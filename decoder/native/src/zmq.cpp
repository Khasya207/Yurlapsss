#include "yurlaps_decoder/zmq.hpp"

#ifdef YURLAPS_WITH_ZMQ
#include <zmq.h>
#endif

namespace yurlaps::decoder {

Publisher::~Publisher() {
#ifdef YURLAPS_WITH_ZMQ
    if (socket_) { zmq_close(socket_); socket_ = nullptr; }
    if (context_) { zmq_ctx_term(context_); context_ = nullptr; }
#endif
}

bool Publisher::bind(std::string_view endpoint, std::string& error) {
#ifdef YURLAPS_WITH_ZMQ
    context_ = zmq_ctx_new();
    socket_ = context_ ? zmq_socket(context_, ZMQ_PUB) : nullptr;
    if (!context_ || !socket_) { error = "ZeroMQ socket gagal dibuat"; return false; }
    const std::string value(endpoint);
    if (zmq_bind(socket_, value.c_str()) != 0) {
        error = zmq_strerror(zmq_errno());
        return false;
    }
    return true;
#else
    (void)endpoint;
    error = "backend ZeroMQ belum dikompilasi";
    return false;
#endif
}

bool Publisher::send(std::string_view message) {
#ifdef YURLAPS_WITH_ZMQ
    if (!socket_) return false;
    return zmq_send(socket_, message.data(), message.size(), 0) >= 0;
#else
    (void)message;
    return false;
#endif
}

}  // namespace yurlaps::decoder
