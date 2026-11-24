#pragma once

#include <string>
#include <string_view>

namespace yurlaps::decoder {

class Publisher {
public:
    Publisher() = default;
    ~Publisher();
    Publisher(const Publisher&) = delete;
    bool bind(std::string_view endpoint, std::string& error);
    bool send(std::string_view message);

private:
    void* context_ = nullptr;
    void* socket_ = nullptr;
};

}  // namespace yurlaps::decoder
