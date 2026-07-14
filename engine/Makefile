CXX      ?= c++
CXXFLAGS ?= -std=c++17 -Wall -Wextra -Wpedantic -O2
INCLUDES := -Iinclude

BUILD_DIR := build

.PHONY: all main test clean

all: main test

main: $(BUILD_DIR)/main
	./$(BUILD_DIR)/main

test: $(BUILD_DIR)/test_order_book
	./$(BUILD_DIR)/test_order_book

$(BUILD_DIR)/main: src/main.cpp src/order_book.cpp include/orderbook/*.hpp | $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) $(INCLUDES) src/main.cpp src/order_book.cpp -o $@

$(BUILD_DIR)/test_order_book: tests/test_order_book.cpp src/order_book.cpp include/orderbook/*.hpp | $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) $(INCLUDES) tests/test_order_book.cpp src/order_book.cpp -o $@

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

clean:
	rm -rf $(BUILD_DIR)
