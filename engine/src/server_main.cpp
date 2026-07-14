#include <cstdlib>
#include <iostream>

#include "orderbook/server.hpp"

int main(int argc, char** argv) {
    uint16_t port = 9999;
    if (argc > 1) port = static_cast<uint16_t>(std::atoi(argv[1]));

    std::cout << "orderbook server listening on port " << port << "\n";
    orderbook::OrderBookServer server(port);
    server.run();
    return 0;
}
