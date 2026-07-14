CXX      ?= c++
CXXFLAGS ?= -std=c++17 -Wall -Wextra -Wpedantic -O2
INCLUDES := -Iinclude
LDFLAGS  := -lpthread

BUILD_DIR := build

CORE_SRCS := src/order_book.cpp src/protocol.cpp src/server.cpp

.PHONY: all main server test test-orderbook test-protocol test-server clean

all: main server test

main: $(BUILD_DIR)/main
	./$(BUILD_DIR)/main

server: $(BUILD_DIR)/orderbook_server

test: test-orderbook test-protocol test-server

test-orderbook: $(BUILD_DIR)/test_order_book
	./$(BUILD_DIR)/test_order_book

test-protocol: $(BUILD_DIR)/test_protocol
	./$(BUILD_DIR)/test_protocol

test-server: $(BUILD_DIR)/test_server
	./$(BUILD_DIR)/test_server

$(BUILD_DIR)/main: src/main.cpp src/order_book.cpp include/orderbook/*.hpp | $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) $(INCLUDES) src/main.cpp src/order_book.cpp -o $@

$(BUILD_DIR)/orderbook_server: src/server_main.cpp $(CORE_SRCS) include/orderbook/*.hpp | $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) $(INCLUDES) src/server_main.cpp $(CORE_SRCS) $(LDFLAGS) -o $@

$(BUILD_DIR)/test_order_book: tests/test_order_book.cpp src/order_book.cpp include/orderbook/*.hpp | $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) $(INCLUDES) tests/test_order_book.cpp src/order_book.cpp -o $@

$(BUILD_DIR)/test_protocol: tests/test_protocol.cpp src/protocol.cpp include/orderbook/*.hpp | $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) $(INCLUDES) tests/test_protocol.cpp src/protocol.cpp -o $@

$(BUILD_DIR)/test_server: tests/test_server.cpp $(CORE_SRCS) include/orderbook/*.hpp | $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) $(INCLUDES) tests/test_server.cpp $(CORE_SRCS) $(LDFLAGS) -o $@

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

clean:
	rm -rf $(BUILD_DIR)
